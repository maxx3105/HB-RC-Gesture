"""Decoder fuer das BIN-RPC-Wireformat, das ReGaHss gegenueber rfd/hs485d spricht.

Das Format ist nicht von eQ-3 dokumentiert; die Implementierung folgt der
Community-Dokumentation (Homegear, pyhomematic) und ist bewusst tolerant:
Unbekannte Typen brechen den Lauf nicht ab, sondern werden als
``Undecoded``-Marker zurueckgegeben, damit ein Mitschnitt auch dann noch
auswertbar bleibt, wenn ein Paket teilweise unverstanden ist.

Aufbau eines Pakets::

    'B' 'i' 'n' <flags:u8> <bodyLength:u32be> <body>

    flags 0x00 = Request, 0x01 = Response, 0xFF = Fault
    flags & 0x40 = dem Body ist ein Header-Block vorangestellt

    Request-Body : <u32be len><methodName> <u32be paramCount> <param>*
    Response-Body: <param>

    param        : <u32be type> <value>
                   0x01 INTEGER  int32be
                   0x02 BOOLEAN  u8
                   0x03 STRING   <u32be len><bytes>
                   0x04 FLOAT    <int32be mantisse><int32be exponent>
                   0x100 ARRAY   <u32be count><param>*
                   0x101 STRUCT  <u32be count>(<u32be len><key><param>)*
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

MAGIC = b"Bin"

FLAG_RESPONSE = 0x01
FLAG_HEADERS = 0x40
FLAG_FAULT = 0xFF

TYPE_INTEGER = 0x01
TYPE_BOOLEAN = 0x02
TYPE_STRING = 0x03
TYPE_FLOAT = 0x04
TYPE_INTEGER64 = 0x0B
TYPE_ARRAY = 0x100
TYPE_STRUCT = 0x101


class BinRpcError(Exception):
    """Der Bytestrom entspricht an dieser Stelle nicht dem erwarteten Format."""


@dataclass
class Undecoded:
    """Platzhalter fuer einen Parameter, dessen Typ nicht bekannt ist."""

    type_id: int
    raw: bytes

    def __repr__(self) -> str:  # pragma: no cover - reine Diagnoseausgabe
        return f"<Undecoded type=0x{self.type_id:x} len={len(self.raw)}>"


@dataclass
class Packet:
    """Ein dekodiertes BIN-RPC-Paket."""

    flags: int
    method: str | None = None
    params: list[Any] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    truncated: bool = False

    @property
    def is_response(self) -> bool:
        return bool(self.flags & FLAG_RESPONSE) and self.flags != FLAG_FAULT

    @property
    def is_fault(self) -> bool:
        return self.flags == FLAG_FAULT

    @property
    def kind(self) -> str:
        if self.is_fault:
            return "FAULT"
        if self.is_response:
            return "RESPONSE"
        return "REQUEST"


class _Reader:
    def __init__(self, buf: bytes, pos: int = 0) -> None:
        self.buf = buf
        self.pos = pos

    def remaining(self) -> int:
        return len(self.buf) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.remaining() < n:
            raise BinRpcError(
                f"Wollte {n} Byte ab Offset {self.pos} lesen, "
                f"es sind nur {self.remaining()} vorhanden"
            )
        chunk = self.buf[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self.take(8))[0]

    def string(self) -> str:
        length = self.u32()
        return self.take(length).decode("utf-8", errors="replace")


def _decode_value(r: _Reader) -> Any:
    type_id = r.u32()

    if type_id == TYPE_INTEGER:
        return r.i32()
    if type_id == TYPE_BOOLEAN:
        return bool(r.u8())
    if type_id == TYPE_STRING:
        return r.string()
    if type_id == TYPE_FLOAT:
        mantissa = r.i32()
        exponent = r.i32()
        return (mantissa / (1 << 30)) * (2.0**exponent)
    if type_id == TYPE_INTEGER64:
        return r.i64()
    if type_id == TYPE_ARRAY:
        count = r.u32()
        return [_decode_value(r) for _ in range(count)]
    if type_id == TYPE_STRUCT:
        count = r.u32()
        out: dict[str, Any] = {}
        for _ in range(count):
            key = r.string()
            out[key] = _decode_value(r)
        return out

    # Unbekannter Typ: Rest als Rohdaten zurueckgeben und Dekodierung hier beenden.
    rest = r.take(r.remaining())
    return Undecoded(type_id, rest)


def _decode_headers(r: _Reader) -> dict[str, str]:
    header_size = r.u32()
    end = r.pos + header_size
    count = r.u32()
    headers: dict[str, str] = {}
    for _ in range(count):
        key = r.string()
        headers[key] = r.string()
    r.pos = min(end, len(r.buf))
    return headers


def decode_packet(data: bytes) -> tuple[Packet, int]:
    """Dekodiert das Paket am Anfang von *data*.

    Gibt das Paket und die Zahl der verbrauchten Bytes zurueck. Wirft
    ``BinRpcError``, wenn kein vollstaendiges Paket vorliegt.
    """
    if len(data) < 8:
        raise BinRpcError("Weniger als 8 Byte - Header unvollstaendig")
    if data[0:3] != MAGIC:
        raise BinRpcError(f"Magic 'Bin' fehlt, gefunden: {data[0:3]!r}")

    flags = data[3]
    body_len = struct.unpack(">I", data[4:8])[0]
    total = 8 + body_len
    truncated = len(data) < total
    body = data[8:total]

    packet = Packet(flags=flags, truncated=truncated)
    r = _Reader(body)

    try:
        # 0xFF ist ein eigenstaendiger Sentinel, kein Bitfeld - das Header-Bit
        # 0x40 ist darin nur zufaellig gesetzt und darf nicht greifen.
        if flags != FLAG_FAULT and (flags & FLAG_HEADERS):
            packet.headers = _decode_headers(r)

        if packet.is_response or packet.is_fault:
            if r.remaining():
                packet.params = [_decode_value(r)]
        else:
            packet.method = r.string()
            count = r.u32()
            packet.params = [_decode_value(r) for _ in range(count)]
    except BinRpcError:
        # Teilweise dekodiert ist besser als gar nicht - Paket so zurueckgeben.
        packet.truncated = True

    return packet, (total if not truncated else len(data))


def iter_packets(data: bytes):
    """Laeuft ueber alle BIN-RPC-Pakete in *data*.

    Bytes vor dem naechsten ``Bin``-Magic werden uebersprungen, damit auch
    Mitschnitte mit Muell am Anfang (abgeschnittene Verbindung, mitgeloggter
    Handshake) noch verwertbar sind.
    """
    pos = 0
    while pos < len(data):
        idx = data.find(MAGIC, pos)
        if idx < 0:
            return
        try:
            packet, used = decode_packet(data[idx:])
        except BinRpcError:
            pos = idx + 1
            continue
        yield idx, packet
        if used <= 0:
            return
        pos = idx + used


def format_packet(packet: Packet, offset: int | None = None) -> str:
    """Einzeilige Kopfzeile plus eingerueckte Parameter, fuer die Konsole."""
    head = packet.kind
    if offset is not None:
        head = f"@{offset:<8} {head}"
    if packet.method:
        head += f" {packet.method}"
    if packet.truncated:
        head += "  [unvollstaendig]"

    lines = [head]
    for i, param in enumerate(packet.params):
        lines.append(f"    [{i}] {_pretty(param, indent=8)}")
    if packet.headers:
        lines.append(f"    headers: {packet.headers}")
    return "\n".join(lines)


def _pretty(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = "\n".join(
            f"{pad}  {k} = {_pretty(v, indent + 4)}" for k, v in value.items()
        )
        return "{\n" + inner + "\n" + pad + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = "\n".join(f"{pad}  {_pretty(v, indent + 4)}" for v in value)
        return "[\n" + inner + "\n" + pad + "]"
    return repr(value)
