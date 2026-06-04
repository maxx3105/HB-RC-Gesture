# HB-RC-Gesture

[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC%20BY--NC--SA%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

AskSin++-basierte **Gesten-Fernbedienung** für Homematic/OpenCCU auf Basis des **GY-PAJ7620U2**-Sensors (Modell `0xFC7E`).

Der PAJ7620U2 erkennt 9 verschiedene Handgesten — jede Geste wird auf einen eigenen `KEY`-Channel gelegt, sodass sich in der CCU pro Geste ein eigenes Programm bzw. eine eigene Direktverknüpfung anlegen lässt.

Basiert auf [AskSin++](https://github.com/pa-pa/AskSinPP).

---

## Features

- **9 KEY-Channels** — pro Geste ein eigener Kanal:

  | Ch | Geste              |
  |----|--------------------|
  | 1  | Up                 |
  | 2  | Down               |
  | 3  | Left               |
  | 4  | Right              |
  | 5  | Forward            |
  | 6  | Backward           |
  | 7  | Clockwise          |
  | 8  | Counter-Clockwise  |
  | 9  | Wave               |

- **Wake-on-Gesture** — der PAJ7620U2 zieht seine `INT`-Leitung beim Erkennen einer Geste low und weckt damit den schlafenden ATmega328P aus dem AskSin++-`Sleep<>`-Modus.
- Software-getriggerte Tastendrücke per `RemoteChannel::state(StateButton<>::pressed/released)` — alle in der CCU gewohnten Auswertungen (`PRESS_SHORT`, `PRESS_LONG`, Direktverknüpfungen) funktionieren ohne Änderungen.

---

## Hardware

### Mikrocontroller

Arduino Pro Mini 3.3 V / 8 MHz (ATmega328P).

### Funkmodul

CC1101 868 MHz auf SPI.

### Verdrahtung

| Pro Mini | CC1101 | PAJ7620U2 | Sonstiges |
|---|---|---|---|
| `D10` | CS  |     |   |
| `D2`  | GDO0 |    |   |
| `D11` | SI (MOSI) |    |   |
| `D12` | SO (MISO) |    |   |
| `D13` | SCK |     |   |
| `A4`  |     | SDA |   |
| `A5`  |     | SCL |   |
| `A0`  |     | INT (Wake) — PCINT8, weckt aus `Sleep<>` |   |
| `D8`  |     |     | Config-Taster gegen GND |
| `D4`  |     |     | Status-LED |
| `3V3` | VCC | VCC |   |
| `GND` | GND | GND |   |

> Der GY-PAJ7620U2 läuft sauber bei 3.3 V. Den Sensor **nicht** an 5 V betreiben.

---

## Software

### Abhängigkeiten

- [AskSin++](https://github.com/pa-pa/AskSinPP)
- [EnableInterrupt](https://github.com/GreyGnome/EnableInterrupt)
- [LowPower](https://github.com/rocketscream/Low-Power)
- `Wire` (Arduino-Core)
- `SPI` (Arduino-Core)

Ein dedizierter PAJ7620-Library-Treiber ist **nicht** erforderlich — der Sensor wird im Sketch über eine kompakte Inline-Init-Sequenz (PROGMEM) angesprochen.

### Geräteinfo anpassen

In `HB-RC-Gesture.ino`:

```cpp
const struct DeviceInfo PROGMEM devinfo = {
  {0xFC, 0x7E, 0x01},   // Device ID  - eindeutig pro Geraet vergeben
  "HBGEST0001",         // Device Serial (10 Zeichen)
  {0xFC, 0x7E},         // Device Model - nicht aendern (matched XML)
  0x10,                 // Firmware Version
  as::DeviceType::Remote,
  {0x01, 0x00}
};
```

### CCU-Geräte-XML

Die Datei `rftypes/hb-rc-gesture.xml` nach `/firmware/rftypes/` auf der CCU kopieren und die `ReGaHss` neu starten (bzw. CCU rebooten). Danach lässt sich das Gerät über den normalen Anlernmodus einbinden.

---

## Status / Offene Punkte

- **Stromverbrauch:** Der PAJ7620U2 zieht im aktiven *Wake-on-Gesture*-Mode ca. 1–2 mA. Für reinen AAA-Dauerbetrieb ist das grenzwertig — falls gewünscht, kann der PAJ-`VCC` über einen MCU-GPIO geschaltet und nur periodisch aktiviert werden.
- **INT-Pulsbreite:** Der INT-Puls des PAJ ist sehr kurz. Bei sporadisch verlorenen Gesten kann zusätzlich ein periodisches Polling per `Alarm` ergänzt werden.

---

## Lizenz

[Creative Commons BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) — analog zu den anderen AskSin++-Selbstbau-Geräten.
