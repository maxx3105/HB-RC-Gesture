#!/bin/sh
#
# Snapshot und Diff der rfd-Persistenz auf der CCU.
#
# Laeuft direkt auf der OpenCCU (busybox ash, POSIX sh - keine Bashismen).
# Beobachtet werden die Pfade, die rfd.conf als Ablage nennt:
#
#   Device Files Dir = /etc/config/rfd     <- die Peer-/Link-Datenbank
#   Address File     = /etc/config/ids
#
# /etc/config/keys wird BEWUSST NICHT mitgesichert - dort liegen die
# AES-Schluessel der Anlage, die haben in einem Analyse-Snapshot nichts
# verloren.
#
# Ablauf:
#   ./linkdb-snapshot.sh take vorher
#   # ... in der WebUI eine Direktverknuepfung anlegen ...
#   ./linkdb-snapshot.sh take nachher
#   ./linkdb-snapshot.sh diff vorher nachher
#
set -eu

SNAPROOT="${SNAPROOT:-/tmp/rfd-snap}"
# Ueberschreibbar, damit sich die Diff-Logik ohne CCU testen laesst.
WATCH="${WATCH:-/etc/config/rfd /etc/config/ids}"

usage() {
	cat <<'EOF'
Verwendung:
  linkdb-snapshot.sh take <label>     Snapshot unter diesem Label ablegen
  linkdb-snapshot.sh list             vorhandene Snapshots zeigen
  linkdb-snapshot.sh diff <a> <b>     zwei Snapshots vergleichen
  linkdb-snapshot.sh clean            alle Snapshots loeschen

Umgebung:
  SNAPROOT   Ablageort der Snapshots (Vorgabe: /tmp/rfd-snap)
EOF
}

# Hexdump, der auf busybox wie auf glibc funktioniert.
dumpfile() {
	if command -v hexdump >/dev/null 2>&1; then
		hexdump -C "$1"
	else
		od -A x -t x1z "$1"
	fi
}

cmd_take() {
	label="$1"
	dest="$SNAPROOT/$label"
	if [ -e "$dest" ]; then
		echo "Snapshot '$label' existiert bereits: $dest" >&2
		exit 1
	fi
	mkdir -p "$dest"

	for path in $WATCH; do
		if [ ! -e "$path" ]; then
			echo "  uebersprungen (nicht vorhanden): $path" >&2
			continue
		fi
		# Zielstruktur unterhalb des Snapshots nachbauen, damit diff sauber laeuft.
		parent="$dest$(dirname "$path")"
		mkdir -p "$parent"
		cp -a "$path" "$parent/" 2>/dev/null || {
			echo "  konnte nicht kopieren: $path" >&2
			continue
		}
		echo "  gesichert: $path" >&2
	done

	# Inventar mit Groessen und Zeitstempeln - oft schon aussagekraeftig genug.
	find "$dest" -type f -exec ls -la {} \; > "$dest/.inventory" 2>/dev/null || true
	date > "$dest/.taken-at"
	echo "Snapshot '$label' abgelegt unter $dest" >&2
}

cmd_list() {
	[ -d "$SNAPROOT" ] || { echo "Keine Snapshots unter $SNAPROOT" >&2; return 0; }
	for d in "$SNAPROOT"/*; do
		[ -d "$d" ] || continue
		printf '%-24s %s\n' "$(basename "$d")" "$(cat "$d/.taken-at" 2>/dev/null || echo '?')"
	done
}

cmd_diff() {
	a="$SNAPROOT/$1"
	b="$SNAPROOT/$2"
	for d in "$a" "$b"; do
		[ -d "$d" ] || { echo "Snapshot fehlt: $d" >&2; exit 1; }
	done

	echo "== Strukturvergleich $1 -> $2"
	# Nur Namen und Groessen - zeigt neue/entfallene Dateien sofort.
	( cd "$a" && find . -type f ! -name '.taken-at' ! -name '.inventory' -exec ls -l {} \; ) \
		| awk '{print $5, $NF}' | sort > /tmp/.snapdiff.a
	( cd "$b" && find . -type f ! -name '.taken-at' ! -name '.inventory' -exec ls -l {} \; ) \
		| awk '{print $5, $NF}' | sort > /tmp/.snapdiff.b
	diff /tmp/.snapdiff.a /tmp/.snapdiff.b || true

	echo
	echo "== Inhaltsvergleich"
	# Die Schleife laeuft in einer Subshell (Pipeline), eine Variable wuerde
	# dort nicht nach aussen durchschlagen - deshalb eine Marker-Datei.
	rm -f /tmp/.snapdiff.changed
	# Alle Dateien beider Seiten betrachten, damit auch neue auffallen.
	( cd "$a" && find . -type f ! -name '.taken-at' ! -name '.inventory' ) > /tmp/.snapdiff.files
	( cd "$b" && find . -type f ! -name '.taken-at' ! -name '.inventory' ) >> /tmp/.snapdiff.files
	sort -u /tmp/.snapdiff.files | while read -r rel; do
		fa="$a/$rel"
		fb="$b/$rel"
		if [ ! -f "$fa" ]; then
			echo "  NEU:       $rel"
			: > /tmp/.snapdiff.changed
			continue
		fi
		if [ ! -f "$fb" ]; then
			echo "  ENTFAELLT: $rel"
			: > /tmp/.snapdiff.changed
			continue
		fi
		if cmp -s "$fa" "$fb"; then
			continue
		fi
		echo "  GEAENDERT: $rel"
		dumpfile "$fa" > /tmp/.snapdiff.ha
		dumpfile "$fb" > /tmp/.snapdiff.hb
		diff -u /tmp/.snapdiff.ha /tmp/.snapdiff.hb | sed 's/^/      /' || true
		: > /tmp/.snapdiff.changed
	done

	[ -f /tmp/.snapdiff.changed ] || echo "  (keine Inhaltsunterschiede)"

	rm -f /tmp/.snapdiff.a /tmp/.snapdiff.b /tmp/.snapdiff.files \
	      /tmp/.snapdiff.ha /tmp/.snapdiff.hb /tmp/.snapdiff.changed
	return 0
}

cmd_clean() {
	rm -rf "$SNAPROOT"
	echo "Geloescht: $SNAPROOT" >&2
}

case "${1:-}" in
	take)  [ $# -eq 2 ] || { usage; exit 1; }; cmd_take "$2" ;;
	list)  cmd_list ;;
	diff)  [ $# -eq 3 ] || { usage; exit 1; }; cmd_diff "$2" "$3" ;;
	clean) cmd_clean ;;
	*)     usage; exit 1 ;;
esac
