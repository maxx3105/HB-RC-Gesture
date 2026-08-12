import struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from binrpc import decode_packet, iter_packets, format_packet, Undecoded

def s(x):
    b = x.encode(); return struct.pack(">I", len(b)) + b
def p_str(x):  return struct.pack(">I", 0x03) + s(x)
def p_int(x):  return struct.pack(">I", 0x01) + struct.pack(">i", x)
def p_bool(x): return struct.pack(">I", 0x02) + bytes([1 if x else 0])
def p_float(m, e): return struct.pack(">I", 0x04) + struct.pack(">i", m) + struct.pack(">i", e)
def p_arr(items): return struct.pack(">I", 0x100) + struct.pack(">I", len(items)) + b"".join(items)
def p_struct(d): return struct.pack(">I", 0x101) + struct.pack(">I", len(d)) + b"".join(s(k)+v for k,v in d.items())
def req(method, params):
    body = s(method) + struct.pack(">I", len(params)) + b"".join(params)
    return b"Bin\x00" + struct.pack(">I", len(body)) + body
def resp(param):
    return b"Bin\x01" + struct.pack(">I", len(param)) + param
def fault(d):
    param = p_struct(d)
    return b"Bin\xff" + struct.pack(">I", len(param)) + param

fails = []
def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r} want {want!r}")
    else:
        print(f"  ok  {name}")

# 1) addLink request, wie ReGaHss ihn absetzen wuerde
pkt, used = decode_packet(req("addLink", [p_str("ABC123:1"), p_str("JEQ0123456:3"), p_str("name"), p_str("desc")]))
check("addLink method", pkt.method, "addLink")
check("addLink kind", pkt.kind, "REQUEST")
check("addLink params", pkt.params, ["ABC123:1", "JEQ0123456:3", "name", "desc"])
check("addLink used==len", used, len(req("addLink", [p_str("ABC123:1"), p_str("JEQ0123456:3"), p_str("name"), p_str("desc")])))

# 2) listDevices response: Array of Structs, gemischte Typen
dev = p_struct({"ADDRESS": p_str("ABC123:1"), "VERSION": p_int(16), "AES_ACTIVE": p_bool(False)})
pkt, _ = decode_packet(resp(p_arr([dev, dev])))
check("listDevices kind", pkt.kind, "RESPONSE")
check("listDevices count", len(pkt.params[0]), 2)
check("listDevices addr", pkt.params[0][0]["ADDRESS"], "ABC123:1")
check("listDevices int", pkt.params[0][0]["VERSION"], 16)
check("listDevices bool", pkt.params[0][0]["AES_ACTIVE"], False)

# 3) Fault - der interessante Fall beim Kreuztest
pkt, _ = decode_packet(fault({"faultCode": p_int(-2), "faultString": p_str("Unknown device")}))
check("fault kind", pkt.kind, "FAULT")
check("fault is_fault", pkt.is_fault, True)
check("fault code", pkt.params[0]["faultCode"], -2)
check("fault string", pkt.params[0]["faultString"], "Unknown device")

# 4) Float-Kodierung: mantisse/2^30 * 2^exp
pkt, _ = decode_packet(resp(p_float(1 << 29, 1)))   # 0.5 * 2 = 1.0
check("float", pkt.params[0], 1.0)

# 5) Header-Flag (0x40) muss uebersprungen werden
body = s("ping") + struct.pack(">I", 0)
hdr = struct.pack(">I", 1) + s("Authorization") + s("Basic xyz")
hdr_block = struct.pack(">I", len(hdr)) + hdr
full = hdr_block + body
pkt, _ = decode_packet(b"Bin\x40" + struct.pack(">I", len(full)) + full)
check("headers method", pkt.method, "ping")
check("headers dict", pkt.headers, {"Authorization": "Basic xyz"})

# 6) Stream mit Muell davor und mehreren Paketen hintereinander
stream = b"\x00\xff garbage " + req("init", [p_str("http://cb/")]) + resp(p_int(0)) + req("listDevices", [])
found = [(o, p.method or p.kind) for o, p in iter_packets(stream)]
check("stream count", len(found), 3)
check("stream methods", [m for _, m in found], ["init", "RESPONSE", "listDevices"])

# 7) Abgeschnittenes Paket darf nicht crashen
whole = req("getLinks", [p_str("ABC123:1"), p_int(0)])
pkt, used = decode_packet(whole[:-5])
check("truncated flagged", pkt.truncated, True)

# 8) Unbekannter Typ -> Undecoded statt Exception
pkt, _ = decode_packet(resp(struct.pack(">I", 0x77) + b"\xde\xad\xbe\xef"))
check("unknown type", isinstance(pkt.params[0], Undecoded), True)

# 9) format_packet darf nicht werfen
pkt, _ = decode_packet(resp(p_arr([dev])))
out = format_packet(pkt, 0)
check("format non-empty", len(out) > 20, True)

print()
if fails:
    print("FEHLER:")
    for f in fails: print("  " + f)
    sys.exit(1)
print("alle Testvektoren bestanden")
