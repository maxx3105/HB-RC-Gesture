#!/usr/bin/env python3
"""Dekodiert einen BIN-RPC-Mitschnitt zwischen ReGaHss und rfd.

Erwartet die Rohdateien, die ``on-ccu/mitm-proxy.sh`` ablegt (je Verbindung
eine Datei pro Richtung). Zeigt an, welche Aufrufe ReGaHss tatsaechlich
absetzt, wenn in der WebUI eine Direktverknuepfung angelegt wird.

Beispiele::

    ./decode_capture.py /pfad/zu/mitschnitt/*.bin
    ./decode_capture.py --filter addLink mitschnitt/*.bin
    ./decode_capture.py --hex mitschnitt/0001.c2s.bin
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from binrpc import format_packet, iter_packets  # noqa: E402


def hexdump(data: bytes, limit: int = 512) -> str:
    lines = []
    view = data[:limit]
    for off in range(0, len(view), 16):
        chunk = view[off : off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {off:08x}  {hexpart}  {text}")
    if len(data) > limit:
        lines.append(f"  ... {len(data) - limit} weitere Byte")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("files", nargs="+", help="Rohdateien des Mitschnitts")
    ap.add_argument(
        "--filter",
        help="nur Pakete zeigen, deren Methodenname diesen Text enthaelt (z.B. addLink)",
    )
    ap.add_argument("--hex", action="store_true", help="zusaetzlich Hexdump der Datei")
    ap.add_argument(
        "--summary",
        action="store_true",
        help="nur zaehlen, welche Methoden wie oft vorkommen",
    )
    args = ap.parse_args()

    counts: dict[str, int] = {}
    total = 0

    for path_str in args.files:
        path = Path(path_str)
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"!! {path}: {exc}", file=sys.stderr)
            continue

        header = f"--- {path}  ({len(data)} Byte)"
        printed_header = False

        if args.hex:
            print(header)
            printed_header = True
            print(hexdump(data))

        for offset, packet in iter_packets(data):
            total += 1
            key = packet.method or f"<{packet.kind.lower()}>"
            counts[key] = counts.get(key, 0) + 1

            if args.summary:
                continue
            if args.filter and args.filter.lower() not in key.lower():
                continue

            if not printed_header:
                print(header)
                printed_header = True
            print(format_packet(packet, offset))
            print()

    print(f"== {total} Pakete", file=sys.stderr)
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"   {count:5d}  {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
