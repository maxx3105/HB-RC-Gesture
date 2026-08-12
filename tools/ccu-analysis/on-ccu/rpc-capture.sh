#!/bin/sh
#
# Mitschnitt des BIN-RPC-Verkehrs zwischen ReGaHss und rfd auf der CCU.
#
# Zwei Betriebsarten:
#
#   sniff  (Vorgabe, ungefaehrlich)
#          Passives Mitlesen auf dem Loopback per tcpdump. Aendert nichts
#          an der Konfiguration und kann die CCU nicht lahmlegen.
#
#   mitm   (Eingriff, nur bewusst verwenden)
#          Biegt BidCos-RF in /etc/config/InterfacesList.xml auf einen
#          lokalen socat-Proxy um, der beide Richtungen roh mitschreibt.
#          Liefert saubere, pro Verbindung getrennte Streams - dafuer
#          muss ReGaHss neu gestartet werden.
#
# ACHTUNG bei 'mitm': solange der Proxy nicht laeuft, erreicht ReGaHss
# das Funkmodul nicht - die CCU zeigt dann keine Funkgeraete mehr. Der
# Originalzustand wird persistent gesichert und mit 'restore'
# zurueckgeholt. Vor dem Reboot immer 'restore' aufrufen.
#
# Die entstandenen Rohdateien mit ../decode_capture.py auswerten.
#
set -eu

OUTDIR="${OUTDIR:-/tmp/rpc-capture}"
PROXY_PORT="${PROXY_PORT:-32999}"
RFD_PORT="${RFD_PORT:-32001}"
IFLIST="/etc/config/InterfacesList.xml"
BACKUP="/etc/config/InterfacesList.xml.precapture"
PIDFILE="/var/run/rpc-capture.pid"

usage() {
	cat <<'EOF'
Verwendung:
  rpc-capture.sh sniff [sekunden]   passiv per tcpdump mitlesen (Vorgabe 120 s)
  rpc-capture.sh mitm               socat-Proxy einhaengen und mitschreiben
  rpc-capture.sh restore            Originalzustand wiederherstellen
  rpc-capture.sh status             zeigen, was gerade aktiv ist

Umgebung:
  OUTDIR      Ablage der Mitschnitte (Vorgabe /tmp/rpc-capture)
  PROXY_PORT  Port des MITM-Proxys   (Vorgabe 32999)
  RFD_PORT    echter rfd-Port        (Vorgabe 32001)
EOF
}

need() {
	command -v "$1" >/dev/null 2>&1 && return 0
	echo "Fehlt: $1" >&2
	echo "  Die OpenCCU bringt das Werkzeug nicht zwingend mit. Entweder ueber" >&2
	echo "  ein Addon nachruesten oder ein statisch gelinktes Binary ablegen." >&2
	return 1
}

# ReGaHss-Startskript suchen, statt den Namen zu raten.
find_rega_init() {
	for f in /etc/init.d/*ReGa* /etc/init.d/*rega* /etc/init.d/S*HMServer; do
		[ -x "$f" ] && { echo "$f"; return 0; }
	done
	return 1
}

restart_rega() {
	init="$(find_rega_init || true)"
	if [ -z "$init" ]; then
		echo "!! Kein ReGaHss-Startskript gefunden." >&2
		echo "   Bitte die CCU von Hand neu starten, damit die Aenderung greift." >&2
		return 0
	fi
	echo "   ReGaHss neu starten: $init restart" >&2
	"$init" restart || {
		echo "!! Neustart fehlgeschlagen - bitte manuell pruefen." >&2
		return 1
	}
}

cmd_sniff() {
	secs="${1:-120}"
	need tcpdump || exit 1
	mkdir -p "$OUTDIR"
	out="$OUTDIR/loopback-$(date +%Y%m%d-%H%M%S).pcap"

	echo "Schneide $secs s Loopback-Verkehr auf Port $RFD_PORT mit ..." >&2
	echo "  Jetzt in der WebUI die Direktverknuepfung anlegen." >&2
	# -U ungepuffert, damit ein Abbruch nicht den halben Mitschnitt verliert.
	tcpdump -i lo -s 0 -U -w "$out" "tcp port $RFD_PORT" &
	tdpid=$!
	# busybox sleep kann keine Bruchteile - ganze Sekunden reichen hier.
	sleep "$secs" || true
	kill "$tdpid" 2>/dev/null || true
	wait "$tdpid" 2>/dev/null || true

	echo "Geschrieben: $out" >&2
	echo "Auswertung: die TCP-Nutzlast aus dem pcap extrahieren, z.B." >&2
	echo "  tshark -r $out -T fields -e tcp.payload | ..." >&2
	echo "und das Ergebnis an decode_capture.py verfuettern." >&2
}

cmd_mitm() {
	need socat || exit 1
	[ -f "$IFLIST" ] || { echo "Nicht gefunden: $IFLIST" >&2; exit 1; }

	if [ -f "$BACKUP" ]; then
		echo "!! $BACKUP existiert bereits - vermutlich laeuft schon ein Mitschnitt." >&2
		echo "   Erst 'restore' aufrufen." >&2
		exit 1
	fi

	mkdir -p "$OUTDIR"
	cp -a "$IFLIST" "$BACKUP"
	echo "Original gesichert: $BACKUP" >&2

	# Nur den Port der BidCos-RF-Zeile umbiegen, alles andere unangetastet lassen.
	sed -i "s|xmlrpc_bin://127\.0\.0\.1:${RFD_PORT}|xmlrpc_bin://127.0.0.1:${PROXY_PORT}|" "$IFLIST"
	if ! grep -q ":${PROXY_PORT}" "$IFLIST"; then
		echo "!! Umschreiben hat nicht gegriffen - stelle Original wieder her." >&2
		cp -a "$BACKUP" "$IFLIST"
		rm -f "$BACKUP"
		exit 1
	fi

	# Beide Richtungen roh wegschreiben, je Verbindung eigene Dateien.
	socat -d TCP-LISTEN:"$PROXY_PORT",fork,reuseaddr \
		SYSTEM:"tee $OUTDIR/\$\$.c2s.bin | socat - TCP:127.0.0.1:$RFD_PORT | tee $OUTDIR/\$\$.s2c.bin" &
	echo $! > "$PIDFILE"
	echo "socat-Proxy laeuft auf Port $PROXY_PORT (PID $(cat "$PIDFILE"))" >&2

	restart_rega || true

	cat >&2 <<EOF

Mitschnitt aktiv. Jetzt in der WebUI die Direktverknuepfung anlegen.
Danach unbedingt:

    $0 restore

Rohdateien: $OUTDIR/*.bin
EOF
}

cmd_restore() {
	if [ -f "$PIDFILE" ]; then
		kill "$(cat "$PIDFILE")" 2>/dev/null || true
		rm -f "$PIDFILE"
		echo "socat beendet." >&2
	fi
	# Sicherheitsnetz, falls der PIDFILE-Weg nicht gegriffen hat.
	pkill -f "TCP-LISTEN:$PROXY_PORT" 2>/dev/null || true

	if [ -f "$BACKUP" ]; then
		cp -a "$BACKUP" "$IFLIST"
		rm -f "$BACKUP"
		echo "InterfacesList.xml wiederhergestellt." >&2
		restart_rega || true
	else
		echo "Kein Backup vorhanden - InterfacesList.xml bleibt unveraendert." >&2
	fi
}

cmd_status() {
	echo "InterfacesList.xml:"
	grep -n "127.0.0.1" "$IFLIST" 2>/dev/null | sed 's/^/  /' || echo "  nicht lesbar"
	echo "Backup vorhanden: $([ -f "$BACKUP" ] && echo ja || echo nein)"
	echo "socat-PID:        $([ -f "$PIDFILE" ] && cat "$PIDFILE" || echo '-')"
	echo "Mitschnitte:"
	ls -la "$OUTDIR" 2>/dev/null | sed 's/^/  /' || echo "  keine"
}

case "${1:-sniff}" in
	sniff)   shift 2>/dev/null || true; cmd_sniff "${1:-120}" ;;
	mitm)    cmd_mitm ;;
	restore) cmd_restore ;;
	status)  cmd_status ;;
	*)       usage; exit 1 ;;
esac
