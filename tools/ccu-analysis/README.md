# CCU-Analyse-Werkzeuge

Werkzeuge, um das Verhalten von `rfd` (BidCos-RF) und `hs485d` (BidCos-Wired)
auf einer OpenCCU zu untersuchen — insbesondere die Frage, warum
Direktverknüpfungen zwischen HM/HB-Funkgeräten und HMW/HBW-Wired-Geräten
nicht möglich sind und wo genau die Sperre sitzt.

Das hat mit der Firmware in diesem Repo nichts zu tun und liegt deshalb in
einem eigenen Unterordner. Es ist reines Analyse-Tooling, kein Bestandteil
des Sketches.

> **Status:** Syntaxgeprüft, aber **nicht gegen eine echte CCU getestet** —
> hier stand keine zur Verfügung. Die BIN-RPC-Dekodierung ist gegen
> selbstgebaute Testvektoren verifiziert, nicht gegen echten ReGaHss-Verkehr.
> Erwarte, dass an Details nachjustiert werden muss.

## Vorher

- **Backup ziehen.** `/etc/config/rfd` ist die Peer-Datenbank; geht die
  kaputt, sind alle Anlernungen weg.
- **Nicht auf der Produktiv-CCU.** Wenn möglich auf einer Testinstanz.
- Reverse Engineering zur Herstellung von Interoperabilität ist in der EU
  ausdrücklich zulässig (Art. 6 RL 2009/24/EG, in Österreich § 40e UrhG).
  Das deckt Analyse und Nachbau der Schnittstelle — nicht die
  Weiterverbreitung von eQ-3-Binärteilen.

## Die Werkzeuge

| Datei | Läuft auf | Zweck |
|---|---|---|
| `rfd_introspect.py` | Arbeitsrechner | API-Oberfläche, Geräte- und Link-Inventar über XML-RPC. Rein lesend. |
| `probe_crossmedia_link.py` | Arbeitsrechner | Der entscheidende Test: nimmt rfd eine Wired-Adresse als Peer an? |
| `binrpc.py` | Bibliothek | Dekoder für das BIN-RPC-Format zwischen ReGaHss und rfd. |
| `decode_capture.py` | Arbeitsrechner | Wertet Mitschnitte mit `binrpc.py` aus. |
| `on-ccu/linkdb-snapshot.sh` | CCU | Snapshot/Diff von `/etc/config/rfd` — zeigt das Link-Speicherformat. |
| `on-ccu/rpc-capture.sh` | CCU | Mitschnitt des ReGaHss↔rfd-Verkehrs (passiv oder per Proxy). |
| `test_binrpc.py` | Arbeitsrechner | Regressionstest des Dekoders gegen selbstgebaute Testvektoren. |

Die Python-Skripte brauchen nur die Standardbibliothek (Python 3.9+).
Die Shell-Skripte sind POSIX-`sh` und laufen unter busybox.

```sh
python3 test_binrpc.py      # 21 Testvektoren, muss ohne Fehler durchlaufen
```

## Empfohlene Reihenfolge

Vom billigsten zum teuersten Erkenntnisgewinn.

### 1. Inventar und Adressformate

```sh
./rfd_introspect.py --host ccu.fritz.box \
    --interface rf --interface wired --devices --links -o inventar.json
```

Zeigt direkt, ob RF-Adressen (3 Byte hex) und Wired-Adressen (4 Byte)
tatsächlich unterschiedlich strukturiert sind — die erste der vermuteten
Sperren.

### 2. Persistenz diffen

Auf die CCU kopieren und dort:

```sh
./linkdb-snapshot.sh take vorher
#  ... in der WebUI eine ganz normale RF↔RF-Direktverknüpfung anlegen ...
./linkdb-snapshot.sh take nachher
./linkdb-snapshot.sh diff vorher nachher
```

Der Hexdump-Diff zeigt das Speicherformat der Peer-Einträge, ohne dass ein
Disassembler nötig wäre. Damit lässt sich beurteilen, ob in einen
Peer-Eintrag überhaupt eine 4-Byte-Adresse passt.

`/etc/config/keys` wird bewusst nicht gesichert — dort liegen die AES-Keys
der Anlage.

### 3. Der Kreuztest

```sh
# Erst Trockenlauf — zeigt nur, was aufgerufen würde
./probe_crossmedia_link.py --host ccu.fritz.box \
    --sender ABC123:1 --receiver JEQ0123456:3

# Dann wirklich absetzen
./probe_crossmedia_link.py --host ccu.fritz.box \
    --sender ABC123:1 --receiver JEQ0123456:3 --execute
```

`addLink` ist schreibend, deshalb ist es hinter `--execute` gesperrt. Sollte
der Aufruf wider Erwarten durchgehen, versucht das Skript sofort ein
`removeLink` und sagt, ob das geklappt hat.

Der Fault-Code beantwortet die eigentliche Frage: kommt die Ablehnung aus
rfd selbst, oder erst aus ReGaHss/WebUI?

### 4. Den RPC-Verkehr mitlesen

Passiv, ohne Eingriff:

```sh
./rpc-capture.sh sniff 120
```

Oder mit Proxy für saubere, pro Verbindung getrennte Streams:

```sh
./rpc-capture.sh mitm
#  ... in der WebUI eine Direktverknüpfung anlegen ...
./rpc-capture.sh restore     # unbedingt, vor allem vor einem Reboot
```

Dann auf dem Arbeitsrechner:

```sh
./decode_capture.py --summary /pfad/zu/*.bin
./decode_capture.py --filter addLink /pfad/zu/*.bin
```

**Warnung zum `mitm`-Modus:** solange `InterfacesList.xml` auf den Proxy
zeigt und der Proxy nicht läuft, erreicht ReGaHss das Funkmodul nicht — die
CCU zeigt dann keine Funkgeräte mehr. Das Original wird nach
`/etc/config/InterfacesList.xml.precapture` gesichert und mit `restore`
zurückgeholt. `status` zeigt jederzeit den aktuellen Zustand.

`socat` und `tcpdump` gehören nicht zum OpenCCU-Grundsystem und müssen
gegebenenfalls nachgerüstet werden; die Skripte prüfen das und sagen es.

### 5. Erst dann statische Analyse

Wenn 1–4 kein eindeutiges Bild ergeben:

```sh
file /bin/rfd
readelf -hd /bin/rfd
strings -n 6 /bin/rfd | grep -iE 'link|peer|INVALID|Unknown'
nm -D /bin/rfd | c++filt          # rfd ist C++, Symbole oft lesbar
```

Danach Ghidra/radare2, Einstieg über den String `"addLink"` und dessen
Xrefs zur Dispatch-Tabelle.

## Ausgangsdaten

Aus `rfd.conf` der OpenCCU:

| Setting | Wert |
|---|---|
| `Listen Port` | `32001` |
| `Device Files Dir` | `/etc/config/rfd` |
| `Device Description Dir` | `/firmware/rftypes` |
| `Address File` | `/etc/config/ids` |
| ComPortFile | `/dev/mmd_bidcos` |

Gestartet aus `/etc/init.d/S61rfd` als `/bin/rfd -f /var/etc/rfd.conf -l 5`.
`hs485d` lauscht analog auf `32000`, `HMIPServer` auf `32010`.

Port `2001` ist nur der extern geproxyte Port; BIN-RPC dort gilt seit
CCU-Firmware 3.41.x als abgekündigt — die Skripte sprechen deshalb direkt
XML-RPC auf `32001`/`32000`.

## Vorarbeit, die man nicht wiederholen muss

- **Homegear-HomeMaticBidCoS** — vollständige offene Reimplementierung von
  BidCoS inklusive Peering (`src/HomeMaticCentral.cpp`).
- **`/firmware/rftypes/*.xml`** auf der CCU — die von eQ-3 selbst
  mitgelieferte, deklarative Beschreibung sämtlicher Frame-Formate.
- **AskSinPP** — die Geräteseite desselben Protokolls.
- **FHEM `CUL_HM`** und **`HM485`** — unabhängige Implementierungen der
  Funk- bzw. der RS485-Seite.
