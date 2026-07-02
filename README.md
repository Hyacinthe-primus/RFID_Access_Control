# RFID Access Control (ESP32-S3)

![C++](https://img.shields.io/badge/Language-C%2B%2B-blue?logo=cplusplus&logoColor=white)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.13-3776AB?style=flat-square&logo=python&logoColor=white)](#)
![ESP32-S3](https://img.shields.io/badge/ESP32-S3-E7352C?logo=espressif&logoColor=white)
![Arduino](https://img.shields.io/badge/Arduino-00979D?logo=arduino&logoColor=white)
![NFC](https://img.shields.io/badge/NFC-PN532-34A853)
![LittleFS](https://img.shields.io/badge/FileSystem-LittleFS-F7931E)
![Embedded](https://img.shields.io/badge/Embedded-Systems-2E8B57)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.md)

A standalone RFID access controller: ESP32-S3 + PN532 (hardware SPI) + 16x2
I2C LCD + a LittleFS-backed JSON user database. The board works completely
on its own with no PC attached; a Python CLI is provided purely for
administration (adding/removing/renaming users, and a card-UID "scan" mode).

---

## 1. Hardware Required

| Component                | Notes                                                        |
|---------------------------|---------------------------------------------------------------|
| ESP32-S3 dev board         | QFN56, rev v0.2, 8MB Octal PSRAM, 40MHz XTAL (as tested). Dual-USB variant (one UART bridge port, one native USB port) |
| PN532 NFC/RFID module V3   | Must be set to **SPI mode** via onboard DIP switches, see §3 |
| 16x2 LCD with PCA8574 I2C backpack | Address is usually `0x27` or `0x3F`, see §7 troubleshooting |
| USB-C / micro-USB cable    | Data-capable, not charge-only                                 |
| Breadboard + jumper wires  |                                                                |

## 2. Software Required

- **Arduino IDE 2.x**
- **ESP32 board package** (via Boards Manager), v2.0.14 or newer recommended
- **Libraries** (Library Manager):
  - `Adafruit PN532`
  - `LiquidCrystal I2C` (the classic Frank de Brabander / Marco Schwartz fork)
  - `ArduinoJson` (v6.x)
- **Python 3.9+** for the CLI, with `pip install -r python_cli/requirements.txt`

---

## 3. PN532: Configuring SPI Mode (Important)

The PN532 V3 breakout has **two DIP switches** near the antenna edge that
select its communication protocol. This is the standard Elechouse/NXP
reference layout; cheap clones sometimes silkscreen it differently, so
cross-check against the legend printed on your specific board before
trusting the table below.

| SW1 | SW2 | Mode                  |
|-----|-----|-----------------------|
| ON  | OFF | **SPI** (use this)    |
| OFF | OFF | HSU (UART)            |
| OFF | ON  | I2C                   |
| ON  | ON  | Reserved / invalid    |

Set **SW1 = ON, SW2 = OFF**. If your board's silkscreen labels the switches
differently (some print "1"/"0" or "SPI"/"I2C"/"HSU" directly next to the
switch), trust the silkscreen over this table.

If `getFirmwareVersion()` in the serial monitor prints `0x0` at boot, the
two most likely causes are (a) DIP switches not in SPI mode, or (b) a
MISO/MOSI swap in wiring.

---

## 4. Wiring

<br/>

> ![Project wiring](Wiring.png)

<br/>

### SPI: ESP32-S3 to PN532 (hardware SPI only, per spec, never software SPI)

| ESP32-S3 GPIO | PN532 Pin | Signal |
|----------------|-----------|--------|
| GPIO 12        | SCK       | SPI Clock |
| GPIO 13        | MISO      | SPI Master In |
| GPIO 11        | MOSI      | SPI Master Out |
| GPIO 10        | SS / NSS  | Chip Select |
| 3V3            | VCC       | Power (do **not** use 5V) |
| GND            | GND       | Ground |

### I2C: ESP32-S3 to 16x2 LCD

| ESP32-S3 GPIO  | LCD Backpack Pin  | Signal |
|----------------|-------------------|--------|
| GPIO 8         | SDA               | I2C Data |
| GPIO 9         | SCL               | I2C Clock |
| 5V             | VCC               | Power |
| GND            | GND               | Ground |


### Power

The LCD backpack requires a **5V power supply**, which is typically not available on the ESP32-S3 onboard regulator pins (unless your development board exposes a raw VBUS/5V pin from the USB). 

To avoid logic level issues and brown-outs:
1. Power the LCD from a separate **5V source** (or the VBUS pin if available and safe).
2. Ensure you **tie all grounds (GND) together** between the external source and the ESP32-S3.
3. If you experience PN532 brown-outs (`getFirmwareVersion()` intermittently returning 0), verify that the ESP32-S3's 3.3V regulator is not being overloaded by other components.


---

## 5. Firmware Setup (Arduino IDE)

1. Copy the entire `RFID_Access_Control/` folder (the one containing
   `RFID_Access_Control.ino`, `Config.h`, `partitions.csv`, etc.). **Do
   not** rename the folder; Arduino IDE requires the folder name to match
   the `.ino` filename.
2. Open `RFID_Access_Control.ino` in Arduino IDE. All the `.h`/`.cpp` files
   will appear as additional tabs automatically, that's expected, this is
   a normal multi-file Arduino sketch, not a broken import.
3. `Tools > Board` then select **"ESP32S3 Dev Module"**.
4. Set these board options under `Tools`:
   - **USB CDC On Boot: Disabled.** This project's `Serial` object is meant
     to run over the UART0 / USB-bridge port (CH34x, CP210x, etc.), not the
     ESP32-S3's native USB peripheral. If your board only breaks out one
     USB connector (wired to the UART bridge chip), enabling this setting
     will silently reroute `Serial` to a port your CLI can never reach and
     every command will time out with no other symptom. Only enable this
     if you have specifically rewritten the firmware to use the native USB
     CDC port instead of UART0, and you are physically connecting to that
     second port.
   - **Flash Size: 16MB (128Mb)**
   - **Partition Scheme: Custom.** This makes the IDE pick up
     `partitions.csv` sitting next to the `.ino` file. This is correct for
     Arduino-ESP32 core 2.0.x/3.x; if your installed core version doesn't
     expose a literal "Custom" entry, look for "Custom (uses
     partitions.csv from sketch)" in the same dropdown. The mechanism is
     the same, only the label differs slightly between core versions.
   - **PSRAM: OPI PSRAM.** Verify this against your specific module; if
     your 8MB PSRAM chip is wired for Quad (not Octal) SPI, boot will
     hang or PSRAM allocations will silently fail. When in doubt, try
     both and check `ESP.getPsramSize()` in serial output at boot.
   - **Upload Speed:** 921600 (or lower if you get upload errors)
5. Install libraries listed in §2 via `Sketch > Include Library > Manage
   Libraries`.
6. `Sketch > Upload`.

### Uploading the LittleFS filesystem image

This project's `users.json` is created *at runtime* by the firmware
itself (see `DatabaseManager::begin()`, it auto-creates an empty valid
DB if none exists), so there is nothing you need to pre-upload for a
fresh board. You do not need the Arduino LittleFS Upload plugin unless
you want to pre-seed `users.json` before first boot; if you do, that
plugin's "Upload LittleFS" command will look for a `data/` folder next
to the sketch containing your seed file.

---

## 6. Python CLI Setup

```bash
cd python_cli
python -m venv venv

# Linux and MacOs
source venv/bin/activate
# Windows:
venv\Scripts\activate

pip install -r requirements.txt
```

### Usage

```bash
python cli.py list                         # list all users
python cli.py add --uid 04AABBCCDD --name "John Smith"
python cli.py add                          # prompts interactively instead
python cli.py remove --uid 04AABBCCDD
python cli.py rename --uid 04AABBCCDD --name "J. Smith"
python cli.py scan                         # present a card, prints its UID
python cli.py --port COM5 list             # override auto-detection
```

The CLI auto-detects the ESP32 by matching known USB VID:PID pairs
(Espressif native USB, CP210x, CH340, CH343) against every serial port on
the system. On boards with two USB connectors, the port that answers is
the one wired to the UART bridge chip (CH34x/CP210x), not the native USB
port, since this firmware runs with USB CDC On Boot disabled. You should
rarely need to look up a COM port manually; if detection fails or picks
the wrong port, run:

```bash
python cli.py list-ports
```

and pass `--port` explicitly.

---

## 7. Serial Protocol Specification

Newline-delimited JSON, one object per line, UTF-8.

**Python to ESP32**

| `type`             | Fields              | Description                          |
|---------------------|---------------------|---------------------------------------|
| `add`               | `uid`, `name`       | Register a new user                   |
| `remove`            | `uid`               | Delete a user                         |
| `rename`            | `uid`, `name`       | Rename an existing user               |
| `list`              | –                    | Request the full user list            |
| `enter_scan_mode`   | –                    | Next card read is reported, not checked against the DB |

**ESP32 to Python**

```json
{"status":"ok"}
{"status":"error","message":"Duplicate UID"}
{"status":"ok","users":[{"uid":"04AABBCCDD","name":"John Smith"}]}
{"status":"ok","type":"uid_detected","uid":"04AABBCCDD"}
```

---

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `getFirmwareVersion()` prints 0 / PN532 not found | DIP switches not set to SPI, or MISO/MOSI swapped |
| LCD shows nothing / garbled boxes | Wrong I2C address (`0x27` vs `0x3F`); run an I2C scanner sketch to confirm. Could also be a wrong contrast potentiometer setting on the backpack |
| CLI connects but every command times out ("No valid response after 3 attempts") | You are very likely on the wrong physical USB port. If your board has two USB connectors, this firmware expects the UART-bridge one (CH34x/CP210x), not the native USB port, because USB CDC On Boot is set to Disabled. Confirm with `list_all_ports()` (see §6) and check the VID against your bridge chip's datasheet, not against Espressif's native USB VID (`0x303A`) |
| CLI can't find the device at all | Run `python cli.py list-ports"` inside `python_cli/` to see all visible ports, then pass `--port` explicitly |
| Upload fails / board not detected by Arduino IDE | Some ESP32-S3 boards need the BOOT button held while the upload starts. Note this is independent of the USB CDC On Boot setting, since esptool always talks to the ROM bootloader over UART0, not native USB |
| `users.json` keeps resetting to empty | Flash was erased/re-partitioned (e.g. partition scheme changed between uploads). LittleFS content doesn't survive a partition table change |

---

## 9. Project Structure

```
RFID_Access_Control/
├── RFID_Access_Control.ino     # setup()/loop() only
├── Config.h                     # pins + constants
├── DatabaseManager.h/.cpp       # LittleFS users.json persistence
├── DisplayManager.h/.cpp        # LCD screen states
├── RFIDManager.h/.cpp           # PN532 hardware-SPI wrapper
├── SerialProtocol.h/.cpp        # newline-JSON framing
├── SystemController.h/.cpp      # the state machine tying it all together
├── partitions.csv               # custom 16MB layout, 12MB LittleFS cap
├── python_cli/
│   ├── cli.py                   # argparse entry point
│   ├── commands.py              # one function per subcommand
│   ├── serial_manager.py        # port auto-detect + retry/timeout I/O
│   ├── protocol.py              # JSON message encode/decode
│   ├── database.py              # typed response parsing
│   ├── utils.py                 # pretty-printing (rich or plain)
│   └── requirements.txt
└── README.md
```

## 10. License

This project is licensed under the MIT License. Do whatever you want with it.