#!/usr/bin/env python3
"""Liest die XML-RPC-Oberflaeche von rfd bzw. hs485d aus - rein lesend.

Ruft nur Abfrage-Methoden auf; es wird nichts angelernt, verknuepft oder
geschrieben. Laeuft vom Arbeitsrechner gegen die CCU, braucht nur die
Python-Standardbibliothek.

Beispiele::

    # Nur die API-Oberflaeche von rfd
    ./rfd_introspect.py --host ccu.fritz.box --methods

    # Geraete- und Link-Inventar beider Interfaces nach JSON
    ./rfd_introspect.py --host ccu.fritz.box --interface rf --interface wired \
        --devices --links -o inventar.json

    # Alles, was zu einem Kanal zu holen ist
    ./rfd_introspect.py --host ccu.fritz.box --address ABC123:1
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import xmlrpc.client
from typing import Any

# rfd und hs485d lauschen laut ihrer jeweiligen .conf auf diesen Ports.
# 2001 ist nur der extern geproxyte Port; BIN-RPC dort gilt seit
# CCU-Firmware 3.41.x als abgekuendigt.
INTERFACES = {
    "rf": ("BidCos-RF", 32001),
    "wired": ("BidCos-Wired", 32000),
    "hmip": ("HmIP-RF", 32010),
}

PARAMSET_TYPES = ("MASTER", "VALUES", "LINK")


class Probe:
    """Duenne Huelle um ServerProxy, die Fehler als Daten statt als Abbruch liefert."""

    def __init__(self, host: str, port: int, name: str, timeout: float) -> None:
        self.name = name
        self.url = f"http://{host}:{port}/"
        self.proxy = xmlrpc.client.ServerProxy(self.url, allow_none=False)
        socket.setdefaulttimeout(timeout)

    def call(self, method: str, *args: Any) -> Any:
        """Ruft *method* auf und verpackt jeden Fehler in ein Ergebnis-Dict."""
        try:
            target = self.proxy
            for part in method.split("."):
                target = getattr(target, part)
            return {"ok": True, "result": target(*args)}
        except xmlrpc.client.Fault as exc:
            return {"ok": False, "fault_code": exc.faultCode, "fault": exc.faultString}
        except (OSError, xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError) as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def collect_methods(probe: Probe) -> dict[str, Any]:
    """Enumeriert die API-Oberflaeche inklusive Hilfetext und Signatur."""
    listing = probe.call("system.listMethods")
    if not listing.get("ok"):
        return {"listMethods": listing}

    methods: dict[str, Any] = {}
    for name in listing["result"]:
        entry: dict[str, Any] = {}
        help_res = probe.call("system.methodHelp", name)
        if help_res.get("ok"):
            entry["help"] = help_res["result"]
        sig_res = probe.call("system.methodSignature", name)
        if sig_res.get("ok"):
            entry["signature"] = sig_res["result"]
        methods[name] = entry
    return {"count": len(methods), "methods": methods}


def classify_address(address: str) -> dict[str, Any]:
    """Beschreibt das Adressformat - der Kern der Frage RF (3 Byte) vs. Wired (4 Byte)."""
    base = address.split(":", 1)[0]
    info: dict[str, Any] = {"address": address, "base": base, "base_len": len(base)}
    try:
        raw = bytes.fromhex(base)
        info["hex_bytes"] = len(raw)
    except ValueError:
        info["hex_bytes"] = None
        info["note"] = "keine reine Hex-Adresse (z.B. Seriennummer-Form)"
    return info


def collect_devices(probe: Probe) -> dict[str, Any]:
    """Holt das Geraeteinventar und leitet daraus die Adressformate ab."""
    res = probe.call("listDevices")
    if not res.get("ok"):
        return {"listDevices": res}

    devices = res["result"]
    formats: dict[str, int] = {}
    for dev in devices:
        addr = dev.get("ADDRESS", "")
        info = classify_address(addr)
        key = f"{info['base_len']} Zeichen / {info['hex_bytes']} Byte hex"
        formats[key] = formats.get(key, 0) + 1

    return {
        "count": len(devices),
        "address_formats": formats,
        "devices": devices,
    }


def collect_links(probe: Probe, devices: list[dict[str, Any]]) -> dict[str, Any]:
    """Sammelt alle bestehenden Direktverknuepfungen je Kanal."""
    links: dict[str, Any] = {}
    for dev in devices:
        addr = dev.get("ADDRESS", "")
        # Nur Kanaele, nicht die Geraete-Wurzel - dort gibt es keine Links.
        if ":" not in addr:
            continue
        res = probe.call("getLinks", addr, 0)
        if res.get("ok") and res["result"]:
            links[addr] = res["result"]
    return {"channels_with_links": len(links), "links": links}


def collect_address(probe: Probe, address: str) -> dict[str, Any]:
    """Alles, was sich zu einem einzelnen Kanal abfragen laesst."""
    out: dict[str, Any] = {"classification": classify_address(address)}
    out["description"] = probe.call("getDeviceDescription", address)
    for pstype in PARAMSET_TYPES:
        out[f"paramsetDescription_{pstype}"] = probe.call(
            "getParamsetDescription", address, pstype
        )
    out["links"] = probe.call("getLinks", address, 0)
    out["linkPeers"] = probe.call("getLinkPeers", address)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="Hostname oder IP der CCU")
    ap.add_argument(
        "--interface",
        action="append",
        choices=sorted(INTERFACES),
        help="Interface, mehrfach angebbar (Vorgabe: rf)",
    )
    ap.add_argument("--methods", action="store_true", help="API-Oberflaeche enumerieren")
    ap.add_argument("--devices", action="store_true", help="Geraeteinventar holen")
    ap.add_argument("--links", action="store_true", help="bestehende Direktverknuepfungen holen")
    ap.add_argument("--address", help="einen einzelnen Kanal komplett abfragen, z.B. ABC123:1")
    ap.add_argument("--timeout", type=float, default=20.0, help="Socket-Timeout in Sekunden")
    ap.add_argument("-o", "--output", help="Ergebnis als JSON in diese Datei schreiben")
    args = ap.parse_args()

    interfaces = args.interface or ["rf"]
    if not (args.methods or args.devices or args.links or args.address):
        # Sinnvolle Vorgabe: das, was fuer die Peering-Frage zaehlt.
        args.devices = args.links = True

    report: dict[str, Any] = {"host": args.host, "interfaces": {}}

    for key in interfaces:
        name, port = INTERFACES[key]
        probe = Probe(args.host, port, name, args.timeout)
        print(f"== {name} ({probe.url})", file=sys.stderr)
        section: dict[str, Any] = {"url": probe.url}

        if args.methods:
            section["api"] = collect_methods(probe)
            api = section["api"]
            print(f"   Methoden: {api.get('count', 'n/a')}", file=sys.stderr)

        devices: list[dict[str, Any]] = []
        if args.devices or args.links:
            dev_section = collect_devices(probe)
            section["devices"] = dev_section
            devices = dev_section.get("devices", []) or []
            print(
                f"   Geraete/Kanaele: {dev_section.get('count', 'n/a')}"
                f"  Adressformate: {dev_section.get('address_formats', {})}",
                file=sys.stderr,
            )

        if args.links:
            section["links"] = collect_links(probe, devices)
            print(
                f"   Kanaele mit Direktverknuepfung: "
                f"{section['links'].get('channels_with_links')}",
                file=sys.stderr,
            )

        if args.address:
            section["address_detail"] = collect_address(probe, args.address)

        report["interfaces"][key] = section

    payload = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"\nGeschrieben: {args.output}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
