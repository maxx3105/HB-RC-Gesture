//- -----------------------------------------------------------------------------------------------------------------------
// HB-RC-Gesture - AskSin++ Gestensensor mit GY-PAJ7620U2
// 9 KEY-Channels: Up / Down / Left / Right / Forward / Backward / CW / CCW / Wave
// ATmega328P (Pro Mini 3.3V) + CC1101 + PAJ7620U2 (I2C) + PCINT-Wake auf A0
//
// Referenz-Pattern: AskSinPP-Geraete/HM-SEC-MDIR/HM-SEC-MDIR.ino (verifiziert)
//- -----------------------------------------------------------------------------------------------------------------------

// =========================================================================
// USER OPTIONS - Pins / Defaults  (Options-First Pattern: VOR den Includes)
// =========================================================================

// #define USE_OTA_BOOTLOADER

// ---- Pin-Belegung (ATmega328P / Pro Mini 3.3V) ----
#define CONFIG_BUTTON_PIN  8        // D8  - PCINT0 - Config-Button gegen GND
#define LED_PIN            4        // D4  - PCINT20 - Status-LED
#define CC1101_CS         10        // D10 - SPI CS
#define CC1101_GDO0        2        // D2  - INT0 - Radio
#define PAJ_INT_PIN       14        // A0  - PCINT8 - PAJ7620 INT/WAKE-OUT
                                    //               (entspricht HM-SEC-MDIR PIR_PIN)

// ---- I2C / PAJ7620U2 ----
#define PAJ_I2C_ADDR       0x73
#define PAJ_REG_BANK_SEL   0xEF
#define PAJ_REG_INT_FLAG_1 0x43
#define PAJ_REG_INT_FLAG_2 0x44

// Gesten-Bits in INT_FLAG_1 / INT_FLAG_2
#define GES_UP        0x01
#define GES_DOWN      0x02
#define GES_LEFT      0x04
#define GES_RIGHT     0x08
#define GES_FORWARD   0x10
#define GES_BACKWARD  0x20
#define GES_CW        0x40
#define GES_CCW       0x80
#define GES_WAVE      0x01  // im INT_FLAG_2

// ---- AskSin++ ----
#define PEERS_PER_CHANNEL  8
#define NUM_CHANNELS       9

// Power-Saving:
// Der PAJ7620U2 laeuft permanent in OP-Mode (~1.4 mA). Der chip-eigene
// OP -> S1 -> S2 Auto-Sleep in der Grove-Init senkt den Verbrauch waehrend
// laengerer Idle-Phasen auf ~1.5 mA (Datasheet 4.3) - macht keinen relevanten
// Unterschied. Schaetzung Batterielaufzeit auf 2× AAA (~2000 mAh): 6-8 Wochen.
//
// Echte Batterielaufzeit (Monate) waere nur erreichbar mit externem
// Wake-Trigger (AM312-PIR + GPIO-MOSFET fuer PAJ-VCC), siehe README.
// Firmware-getriebener Suspend-Cycle wurde getestet und verworfen: der PAJ-
// Gesten-Algorithmus braucht stabilen Hintergrund/AE/Pipeline-Zustand, der
// bei jedem Suspend verloren geht - Detektion wird unzuverlaessig und
// Klassifikation falsch.

// =========================================================================
// Includes
// =========================================================================

#define EI_NOTEXTERNAL
#include <EnableInterrupt.h>
#include <SPI.h>
#include <Wire.h>
#include <AskSinPP.h>
#include <LowPower.h>
#include <Register.h>
#include <MultiChannelDevice.h>
#include <Remote.h>

// =========================================================================

// Channel-Mapping
enum GestureChannel : uint8_t {
  CH_UP        = 1,
  CH_DOWN      = 2,
  CH_LEFT      = 3,
  CH_RIGHT     = 4,
  CH_FORWARD   = 5,
  CH_BACKWARD  = 6,
  CH_CW        = 7,
  CH_CCW       = 8,
  CH_WAVE      = 9
};

// PAJ7620U2 Standard-Init-Sequenz (aus Datasheet / Grove-Library, gekuerzt auf das Wesentliche)
// Format: { register, value }
static const uint8_t PROGMEM initRegisterArray[][2] = {
  {0xEF, 0x00},
  {0x37, 0x07}, {0x38, 0x17}, {0x39, 0x06}, {0x41, 0x00}, {0x42, 0x00},
  {0x46, 0x2D}, {0x47, 0x0F}, {0x48, 0x3C}, {0x49, 0x00}, {0x4A, 0x1E},
  {0x4C, 0x20}, {0x51, 0x10}, {0x5E, 0x10}, {0x60, 0x27}, {0x80, 0x42},
  {0x81, 0x44}, {0x82, 0x04}, {0x8B, 0x01}, {0x90, 0x06}, {0x95, 0x0A},
  {0x96, 0x0C}, {0x97, 0x05}, {0x9A, 0x14}, {0x9C, 0x3F}, {0xA5, 0x19},
  {0xCC, 0x19}, {0xCD, 0x0B}, {0xCE, 0x13}, {0xCF, 0x64}, {0xD0, 0x21},
  {0xEF, 0x01},
  {0x02, 0x0F}, {0x03, 0x10}, {0x04, 0x02}, {0x25, 0x01}, {0x27, 0x39},
  {0x28, 0x7F}, {0x29, 0x08}, {0x3E, 0xFF}, {0x5E, 0x3D}, {0x65, 0x96},
  {0x67, 0x97}, {0x69, 0xCD}, {0x6A, 0x01}, {0x6D, 0x2C}, {0x6E, 0x01},
  {0x72, 0x01}, {0x73, 0x35}, {0x74, 0x00}, {0x77, 0x01},
  // Gesten-Mode: Normal Speed (~120 Hz), Empfindlichkeit Standard
  {0xEF, 0x00},
  {0x41, 0xFF}, {0x42, 0x01}   // INT-Flags aktiv: alle Gesten + Wave
};

using namespace as;

const struct DeviceInfo PROGMEM devinfo = {
  {0xFC, 0x7E, 0x01},          // Device ID
  "HBGEST0001",                // Device Serial
  {0xFC, 0x7E},                // Device Model
  0x10,                        // Firmware Version
  as::DeviceType::Remote,      // Device Type
  {0x01, 0x00}                 // Info Bytes
};

typedef AskSin<StatusLed<LED_PIN>, BatterySensor, Radio<LibSPI<CC1101_CS>, CC1101_GDO0>> Hal;

typedef RemoteChannel<Hal, PEERS_PER_CHANNEL, List0> ChannelType;
typedef MultiChannelDevice<Hal, ChannelType, NUM_CHANNELS> GestureDevice;

Hal hal;
GestureDevice sdev(devinfo, 0x20);
ConfigButton<GestureDevice> cfgBtn(sdev);

// PAJ-Wake-Flag + ISR-Counter
volatile bool     pajWoke     = false;
volatile uint16_t pajIsrCount = 0;

static void pajISR() {
  pajWoke = true;
  pajIsrCount++;
}

// ---- I2C-Helper ----
static bool pajWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(PAJ_I2C_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

static bool pajRead(uint8_t reg, uint8_t& val) {
  Wire.beginTransmission(PAJ_I2C_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)PAJ_I2C_ADDR, (uint8_t)1) != 1) return false;
  val = Wire.read();
  return true;
}

static bool pajInit() {
  // Wake-Sequenz: Chip braucht nach Power-On ~700us, einmal lesen reicht
  uint8_t dummy;
  pajRead(0x00, dummy);

  // Part-ID lesen (Bank 0, Reg 0x00..0x01 = 0x20 0x76)
  pajWrite(PAJ_REG_BANK_SEL, 0x00);
  uint8_t id_l = 0, id_h = 0;
  pajRead(0x00, id_l);
  pajRead(0x01, id_h);
  if (!(id_l == 0x20 && id_h == 0x76)) {
    DPRINT(F("PAJ7620 not found: 0x")); DHEX(id_h); DHEX(id_l); DPRINTLN("");
    return false;
  }

  // Init-Sequenz schreiben
  for (uint16_t i = 0; i < sizeof(initRegisterArray) / 2; i++) {
    uint8_t r = pgm_read_byte(&initRegisterArray[i][0]);
    uint8_t v = pgm_read_byte(&initRegisterArray[i][1]);
    pajWrite(r, v);
  }
  pajWrite(PAJ_REG_BANK_SEL, 0x00);
  DPRINTLN(F("PAJ7620 ready"));
  return true;
}

// Software-Trigger eines KEY-Channels: kurzer Tastendruck
static void triggerChannel(uint8_t chnum) {
  DPRINT(F("Gesture ch=")); DDECLN(chnum);
  sdev.channel(chnum).state(StateButton<>::pressed);
  sdev.channel(chnum).state(StateButton<>::released);
}

static void handleGesture() {
  uint16_t n = pajIsrCount;

  uint8_t f1 = 0, f2 = 0;
  bool ok1 = pajRead(PAJ_REG_INT_FLAG_1, f1);
  bool ok2 = pajRead(PAJ_REG_INT_FLAG_2, f2);

  // Loggen nur bei echter Aktivitaet (Flags != 0 oder I2C-Fehler).
  // Reine "leere Check-Windows" werden nicht gelogged - sonst spammen sie
  // den Serial Monitor mit ~3-4 Zeilen/s im Idle.
  if (f1 != 0 || f2 != 0 || !ok1 || !ok2) {
    DPRINT(F("PAJ isr#=")); DDEC(n);
    DPRINT(F(" f1=0x")); DHEX(f1);
    DPRINT(F(" f2=0x")); DHEX(f2);
    if (!ok1 || !ok2) DPRINT(F(" [I2C-ERR]"));
    DPRINTLN("");
  }

  if      (f1 & GES_UP)        triggerChannel(CH_UP);
  else if (f1 & GES_DOWN)      triggerChannel(CH_DOWN);
  else if (f1 & GES_LEFT)      triggerChannel(CH_LEFT);
  else if (f1 & GES_RIGHT)     triggerChannel(CH_RIGHT);
  else if (f1 & GES_FORWARD)   triggerChannel(CH_FORWARD);
  else if (f1 & GES_BACKWARD)  triggerChannel(CH_BACKWARD);
  else if (f1 & GES_CW)        triggerChannel(CH_CW);
  else if (f1 & GES_CCW)       triggerChannel(CH_CCW);
  else if (f2 & GES_WAVE)      triggerChannel(CH_WAVE);
}

void setup() {
  DINIT(57600, ASKSIN_PLUS_PLUS_IDENTIFIER);
  sdev.init(hal);

  // Config-Button und PAJ-INT registrieren VOR initDone() - identisches Pattern
  // wie HM-SEC-MDIR (motionISR fuer PIR, gleicher Aufbau).
  buttonISR(cfgBtn, CONFIG_BUTTON_PIN);

  // PAJ-INT auf A0 (= D14, PCINT8). EI_NOTEXTERNAL ist gesetzt, EnableInterrupt-
  // Lib nutzt fuer Nicht-INT0/1-Pins automatisch PCINT - PCINT-Change weckt auch
  // aus SLEEP_MODE_PWR_DOWN sauber auf (das ist auf D2/D3 mit Edge nicht garantiert).
  pinMode(PAJ_INT_PIN, INPUT_PULLUP);
  enableInterrupt(PAJ_INT_PIN, pajISR, FALLING);

  sdev.initDone();

  // --- "langsame" Initialisierung NACH initDone() ---
  // (Memory: AskSin++ EPD setup()-Reihenfolge)
  Wire.begin();
  Wire.setClock(400000);
  pajInit();
}

void loop() {
  bool worked = hal.runready();
  bool poll   = sdev.pollRadio();

  if (pajWoke) {
    pajWoke = false;
    handleGesture();
  }

  if (worked == false && poll == false) {
    // Sleep<> = PWR_DOWN - PCINT8 (A0) weckt sauber aus PWR_DOWN.
    hal.activity.savePower<Sleep<>>(hal);
  }
}
