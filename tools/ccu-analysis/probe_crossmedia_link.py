#!/usr/bin/env python3
"""Das entscheidende Experiment: laesst rfd eine Wired-Adresse als Peer zu?

Beantwortet empirisch, ob die Ablehnung medienuebergreifender
Direktverknuepfungen in rfd/hs485d selbst sitzt oder erst in ReGaHss/WebUI.
Ohne Disassembler.

Vorgehen:

1. Beide Interfaces nach ihren Geraeten fragen und die Adressformate
   gegenueberstellen (RF: 3 Byte hex, Wired: 4 Byte).
2. ``getParamsetDescription(<kanal>, "LINK")`` auf beiden Seiten - existiert
   ueberhaupt ein LINK-Paramset?
3. ``addLink(rf_sender, wired_receiver)`` gegen rfd und die Gegenrichtung
   gegen hs485d - und den exakten Fault-Code protokollieren.

Schritt 3 ist schreibend und laeuft nur mit ``--execute``. Ohne das Flag
wird lediglich angezeigt, was aufgerufen wuerde. Mit ``--execute`` versucht
das Skript anschliessend, einen versehentlich entstandenen Link per
``removeLink`` wieder zu entfernen, und meldet, ob das geklappt hat.

Beispiel::

    ./probe_crossmedia_link.py --host ccu.fritz.box \
        --sender ABC123:1 --receiver JEQ0123456:3
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import xmlrpc.client
from typing import Any

RF_PORT = 32001
WIRED_PORT = 32000


def proxy(host: str, port: int, timeout: float) -> xmlrpc.client.ServerProxy:
    socket.setdefaulttimeout(timeout)
    return xmlrpc.client.ServerProxy(f"http://{host}:{port}/")


def call(px: xmlrpc.client.ServerProxy, method: str, *args: Any) -> dict[str, Any]:
    """Ruft *method* auf; jeder Fehler wird zu einem Ergebnis-Dict statt zu einer Exception."""
    try:
        return {"ok": True, "result": getattr(px, method)(*args)}
    except xmlrpc.client.Fault as exc:
        return {
            "ok": False,
            "fault_code": exc.faultCode,
            "fault_string": exc.faultString,
        }
    except (OSError, xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def describe_address(address: str) -> dict[str, Any]:
    base = address.split(":", 1)[0]
    out: dict[str, Any] = {"address": address, "base": base, "base_len": len(base)}
    try:
        out["hex_bytes"] = len(bytes.fromhex(base))
    except ValueError:
        out["hex_bytes"] = None
    return out


def step_inventory(host: str, timeout: float) -> dict[str, Any]:
    """Adressformate beider Interfaces nebeneinanderlegen."""
    out: dict[str, Any] = {}
    for label, port in (("BidCos-RF", RF_PORT), ("BidCos-Wired", WIRED_PORT)):
        px = proxy(host, port, timeout)
        res = call(px, "listDevices")
        if not res["ok"]:
            out[label] = {"reachable": False, "detail": res}
            print(f"  {label:14s} nicht erreichbar: {res}", file=sys.stderr)
            continue
        devices = res["result"]
        samples = [d.get("ADDRESS", "") for d in devices[:5]]
        formats: dict[str, int] = {}
        for dev in devices:
            info = describe_address(dev.get("ADDRESS", ""))
            key = f"{info['base_len']}z/{info['hex_bytes']}B"
            formats[key] = formats.get(key, 0) + 1
        out[label] = {
            "reachable": True,
            "count": len(devices),
            "address_formats": formats,
            "samples": samples,
        }
        print(
            f"  {label:14s} {len(devices):4d} Eintraege, Formate {formats}",
            file=sys.stderr,
        )
    return out


def step_link_paramsets(
    host: str, sender: str, receiver: str, timeout: float
) -> dict[str, Any]:
    """Existiert auf beiden Seiten ueberhaupt ein LINK-Paramset?"""
    out: dict[str, Any] = {}
    for label, port, addr in (
        ("sender@rf", RF_PORT, sender),
        ("receiver@wired", WIRED_PORT, receiver),
    ):
        px = proxy(host, port, timeout)
        res = call(px, "getParamsetDescription", addr, "LINK")
        if res["ok"]:
            params = sorted(res["result"].keys()) if isinstance(res["result"], dict) else res["result"]
            out[label] = {"ok": True, "parameters": params}
            print(f"  {label:16s} LINK-Paramset: {params}", file=sys.stderr)
        else:
            out[label] = res
            print(f"  {label:16s} LINK-Paramset nicht lesbar: {res}", file=sys.stderr)
    return out


def step_addlink(
    host: str, sender: str, receiver: str, timeout: float, execute: bool
) -> dict[str, Any]:
    """Der eigentliche Test - nur mit --execute wirklich abgesetzt."""
    attempts = [
        {
            "label": "rfd: RF-Sender -> Wired-Empfaenger",
            "port": RF_PORT,
            "args": (sender, receiver, "xmedia-probe", "Testlauf Analyse"),
        },
        {
            "label": "hs485d: Wired-Sender -> RF-Empfaenger",
            "port": WIRED_PORT,
            "args": (receiver, sender, "xmedia-probe", "Testlauf Analyse"),
        },
    ]

    results = []
    for att in attempts:
        entry: dict[str, Any] = {"label": att["label"], "call": f"addLink{att['args']}"}
        if not execute:
            entry["skipped"] = "Trockenlauf - mit --execute wirklich absetzen"
            print(f"  [dry-run] {att['label']}: addLink{att['args']}", file=sys.stderr)
            results.append(entry)
            continue

        px = proxy(host, int(att["port"]), timeout)
        res = call(px, "addLink", *att["args"])
        entry["result"] = res
        print(f"  {att['label']}: {res}", file=sys.stderr)

        if res.get("ok"):
            # Unerwartet erfolgreich - sofort wieder aufraeumen.
            cleanup = call(px, "removeLink", att["args"][0], att["args"][1])
            entry["cleanup"] = cleanup
            print(f"    -> Link entstanden, removeLink: {cleanup}", file=sys.stderr)
            if not cleanup.get("ok"):
                print(
                    "    !! Aufraeumen fehlgeschlagen - Verknuepfung bitte in der "
                    "WebUI pruefen und von Hand loeschen",
                    file=sys.stderr,
                )
        results.append(entry)

    return {"executed": execute, "attempts": results}


def interpret(report: dict[str, Any]) -> list[str]:
    """Aus den Rohdaten die Aussage ableiten, um die es geht."""
    notes: list[str] = []

    inv = report.get("inventory", {})
    rf = inv.get("BidCos-RF", {})
    wired = inv.get("BidCos-Wired", {})
    if rf.get("reachable") and wired.get("reachable"):
        notes.append(
            f"Adressformate: RF {rf.get('address_formats')} vs. "
            f"Wired {wired.get('address_formats')}"
        )
    if not wired.get("reachable"):
        notes.append(
            "hs485d nicht erreichbar - ohne BidCos-Wired-Interface ist der "
            "Kreuztest nicht aussagekraeftig."
        )

    for att in report.get("addlink", {}).get("attempts", []):
        res = att.get("result")
        if not res:
            continue
        if res.get("ok"):
            notes.append(
                f"{att['label']}: addLink wurde ANGENOMMEN - die Sperre sitzt "
                f"dann nicht im Daemon, sondern weiter oben (ReGaHss/WebUI)."
            )
        elif "fault_code" in res:
            notes.append(
                f"{att['label']}: abgelehnt vom Daemon selbst, "
                f"Fault {res['fault_code']}: {res['fault_string']}"
            )
    return notes


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--host", required=True, help="Hostname oder IP der CCU")
    ap.add_argument("--sender", required=True, help="RF-Sendekanal, z.B. ABC123:1")
    ap.add_argument("--receiver", required=True, help="Wired-Empfangskanal, z.B. JEQ0123456:3")
    ap.add_argument(
        "--execute",
        action="store_true",
        help="addLink wirklich absetzen (schreibend!). Ohne dieses Flag nur Trockenlauf.",
    )
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("-o", "--output", help="Vollen Bericht als JSON hierhin schreiben")
    args = ap.parse_args()

    report: dict[str, Any] = {"host": args.host, "sender": args.sender, "receiver": args.receiver}

    print("[1/3] Geraeteinventar und Adressformate", file=sys.stderr)
    report["inventory"] = step_inventory(args.host, args.timeout)

    print("[2/3] LINK-Paramsets", file=sys.stderr)
    report["link_paramsets"] = step_link_paramsets(
        args.host, args.sender, args.receiver, args.timeout
    )

    print("[3/3] addLink ueber die Medien-Grenze", file=sys.stderr)
    if not args.execute:
        print(
            "      Trockenlauf. --execute setzt die Aufrufe wirklich ab und "
            "raeumt danach per removeLink auf.",
            file=sys.stderr,
        )
    report["addlink"] = step_addlink(
        args.host, args.sender, args.receiver, args.timeout, args.execute
    )

    report["interpretation"] = interpret(report)
    print("\n== Befund", file=sys.stderr)
    for note in report["interpretation"]:
        print(f"  - {note}", file=sys.stderr)

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
