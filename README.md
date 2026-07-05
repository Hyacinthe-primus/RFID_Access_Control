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
administration (adding/removing/renaming users, batch import/export,
provisioning Wi-Fi, NTP time management, and a card-UID "scan" mode). Users
have an expiration date, checked against an NTP-synced clock, and a passive
buzzer gives audible pass/fail feedback. The database supports up to **2000
users** (RAM limit on ESP32-S3 SRAM).

---

## 1. Hardware Required

| Component                | Notes                                                        |
|---------------------------|---------------------------------------------------------------|
| ESP32-S3 dev board         | QFN56, rev v0.2, 8MB Octal PSRAM, 16MB Flash, 40MHz XTAL (as tested) |
| PN532 NFC/RFID module V3   | Must be set to **SPI mode** via onboard DIP switches, see Section3 |
| 16x2 LCD with PCA8574 I2C backpack | Address is usually `0x27` or `0x3F`, see Section7 troubleshooting |
| Passive buzzer              | Connected to GPIO 6, see Section4b |
| USB-C / micro-USB cable    | Data-capable, not charge-only                                 |
| Breadboard + jumper wires  |                                                                |
| Wi-Fi network (2.4GHz)     | Required for NTP time sync -- see Section6b                   |

## 2. Software Required

- **Arduino IDE 2.x**
- **ESP32 board package** (via Boards Manager), v2.0.14 or newer recommended
- **Libraries** (Library Manager):
  - `Adafruit PN532`
  - `LiquidCrystal I2C` (the classic Frank de Brabander / Marco Schwartz fork)
  - `ArduinoJson` (v6.x)
  - `WiFi` and `Preferences` -- both ship with the ESP32 board package, no
    separate install needed
- **Python 3.9+** for the CLI, with `pip install -r python_cli/requirements.txt`

---

## 3. PN532: Configuring SPI Mode (Important)

The PN532 V3 breakout has **two DIP switches** near the antenna edge that
select its communication protocol.

| SW1 | SW2 | Mode                  |
|-----|-----|-----------------------|
| ON  | OFF | **SPI** (use this)    |
| OFF | OFF | HSU (UART)            |
| OFF | ON  | I2C                   |
| ON  | ON  | Reserved / invalid    |

Set **SW1 = ON, SW2 = OFF**. If `getFirmwareVersion()` prints `0x0` at boot,
the two most likely causes are (a) DIP switches not in SPI mode, or (b) a
MISO/MOSI swap in wiring.

---

## 4. Wiring

<br/>

> ![Project wiring](Wiring.png)

<br/>

### SPI: ESP32-S3 to PN532

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

### 4b. Buzzer (passive)

| ESP32-S3 GPIO | Buzzer Pin | Signal |
|----------------|-----------|--------|
| GPIO 6         | Signal/+  | PWM tone (driven via `tone()`/`ledc`) |
| GND            | -         | Ground |

Must be a **passive** buzzer (driven by a PWM tone), not an active buzzer.
Access Granted plays a 1.5s ascending 4-note tone; Access Denied plays a
clearly different 1.5s descending 4-note tone.

### Power

The LCD backpack requires a **5V power supply**. To avoid logic level issues:
1. Power the LCD from a separate **5V source** (or the VBUS pin if available).
2. Tie all grounds (GND) together between the external source and the ESP32-S3.

---

## 5. Firmware Setup (Arduino IDE)

1. Copy the entire `RFID_Access_Control/` folder. **Do not** rename it;
   Arduino IDE requires the folder name to match the `.ino` filename.
2. Open `RFID_Access_Control.ino` in Arduino IDE.
3. `Tools > Board` then select **"ESP32S3 Dev Module"**.
4. Set these board options under `Tools`:
   - **USB CDC On Boot: Disabled**
   - **Flash Size: 16MB (128Mb)**
   - **Partition Scheme: Custom** (uses `partitions.csv` from sketch)
   - **PSRAM: OPI PSRAM**
   - **Upload Speed:** 921600
5. Install libraries listed in Section2.
6. `Sketch > Upload`.

### 5a. Serial Port Configuration

The firmware uses **921600 baud** by default (`SERIAL_BAUD` in `Config.h`).
The Python CLI auto-matches this rate. If you change it in `Config.h`, the
CLI will still connect (it auto-detects), but mismatched rates cause garbled
output.

---

## 6. Python CLI Setup

```bash
cd python_cli
python -m venv venv

# Linux / macOS
source venv/bin/activate
# Windows:
venv\Scripts\activate

pip install -r requirements.txt
```

### Usage

```bash
# User management
python cli.py list                                          # list all users
python cli.py add --uid 04AABBCCDD --name "John Smith" --valid-days 30
python cli.py add --uid 5AF73581 --name "Hyace"             # ADMIN badge (no expiry)
python cli.py add                                          # prompts interactively
python cli.py remove --uid 04AABBCCDD
python cli.py remove --force                                # wipe ALL users
python cli.py remove --except 04AABBCCDD,5AF73581           # keep only these UIDs
python cli.py rename --uid 04AABBCCDD --name "J. Smith"
python cli.py find --name "Smith"                           # find users by name (partial match)

# Batch import / export
python cli.py export users.json                             # dump device DB to JSON
python cli.py import users.json                             # import from JSON or CSV
python cli.py import users.json --dry-run                   # validate file without writing
python cli.py import users.json --clear                     # wipe DB before importing

# Device info
python cli.py status                                        # DB path + LittleFS storage usage
python cli.py netstatus                                     # Wi-Fi connected? SSID? IP? signal?
python cli.py ntp-time                                      # show device's current local time
python cli.py ntp-sync                                      # force NTP resync

# Wi-Fi provisioning
python cli.py configure -w "MyWiFi" -p "MyPassword"

# Card scanning
python cli.py scan                                          # present a card, prints its UID
python cli.py scan --timeout 5                              # scan card for a set time
python cli.py scan --infinite                               # scan cards forever, Ctrl+C to stop

# Debug
python cli.py list-ports                                    # list all serial ports
python cli.py --port COM5 list             # override auto-detection
```

### 6a. User Schema and Expiration

Each user is stored on the device as:

```json
{
  "uid": "A43FE5S4",
  "name": "Azrael",
  "registered": "2024-04-06",
  "valid_days": 30
}
```

- **`registered`** -- ISO-8601 date (`YYYY-MM-DD`), stamped automatically at
  add-time from the CLI machine's local date. Never entered manually.
- **`valid_days`** -- days from `registered` the badge stays valid. Accepts
  decimals (`0.01` = ~14 minutes, useful for testing).

Expiration is evaluated on the device using its NTP-synced clock:

```
expiration_date = registered + valid_days

if current_date_time <= expiration_date:
    Access Granted
else:
    Access Denied (Expired)
```

If the ESP32 hasn't completed an NTP sync, it **fails safe**: every normal
card is denied with "No Time Sync".

`NTP_GMT_OFFSET_SEC` in `Config.h` (default 0 = UTC) must match the
timezone of the machine running the CLI, or expiration will be off.

#### Admin badges

Omit `--valid-days` to create an admin card (no expiration, always granted):

```bash
python cli.py add --uid 5AF73581 --name "Hyace"
```

Admin badges work even without NTP sync. Stored with sentinel values
(`registered=""`, `valid_days=-1`).

### 6b. Batch Import / Export

The `import` command reads a JSON file (matching the `users.json` schema)
and sends all users to the device in a single batch:

```bash
python cli.py import users.json
```

This opens **one serial connection** and uses the `import_begin`/`import_end`
protocol to write the database to flash **once** at the end, instead of
multiple separate writes. 

CSV files are also supported (auto-detected by extension):

```csv
uid,name,registered,valid_days
04AABBCCDD,Alice,2025-01-15,30
5AF73581,Bob,,
```

The `export` command dumps the device database to a JSON file:

```bash
python cli.py export backup.json
```

### 6b2. User Limit

The device supports a maximum of **2000 users** (`MAX_USERS` in `Config.h`).
This is a RAM limit: each user record consumes ~380 bytes of SRAM. Exceeding
it causes heap overflow and device reboot.

The CLI checks the limit before importing and refuses if the total would
exceed 2000. Use `--clear` to wipe the DB first if needed:

```bash
python cli.py import users.json --clear
```

If a single `add` or `import` exceeds the limit, the device responds with
"Database full (max 2000 users)" on the LCD and in the CLI.

### 6c. Wi-Fi Provisioning and NTP Time Sync

1. `python cli.py configure -w "MyWiFi" -p "MyPassword"` provisions
   credentials on the device (stored in NVS, persist across reboots).
2. On every boot, if credentials are stored, the device reconnects and
   re-syncs its clock via NTP automatically.
3. Re-sync happens every 6 hours (`NTP_RESYNC_INTERVAL_MS`).

Use `ntp-time` to check the device's current time, and `ntp-sync` to force
a resync if the clock drifted or the initial sync failed:

```bash
python cli.py ntp-time       # Device time: 2026-07-03 19:43:20 (epoch: ...)
python cli.py ntp-sync       # Force NTP resync
```

---

## 7. Serial Protocol Specification

Newline-delimited JSON, one object per line, UTF-8. Baud rate: 921600.

**Python to ESP32**

| `type`             | Fields                          | Description                          |
|---------------------|----------------------------------|---------------------------------------|
| `add`               | `uid`, `name`, `registered`*, `valid_days`* | Register a new user. `registered`/`valid_days` omitted = admin badge. |
| `remove`            | `uid`               | Delete a user                         |
| `clear_all`         | –                    | Delete ALL users (sent by `remove --force`) |
| `remove_all_except` | `uids` (array)      | Delete every user NOT in `uids` |
| `rename`            | `uid`, `name`       | Rename an existing user               |
| `list`              | –                    | Request the full user list            |
| `enter_scan_mode`   | –                    | Next card read is reported, not checked against the DB |
| `status`            | –                    | Request DB path + LittleFS storage usage |
| `net_status`        | –                    | Request Wi-Fi connection state |
| `get_time`          | –                    | Request device's current local time |
| `ntp_sync`          | –                    | Force NTP resync |
| `import_begin`      | –                    | Enter batch-import mode (no per-add flash writes) |
| `import_end`        | –                    | Finalize import: persist to flash once, report count |
| `configure_wifi`    | `ssid`, `password`  | Store Wi-Fi credentials and connect |

*`registered` and `valid_days` are optional in `add`. When either is missing
the firmware treats the badge as admin.

**ESP32 to Python**

```json
{"status":"ok"}
{"status":"error","message":"Duplicate UID"}
{"status":"ok","users":[...]}
{"status":"ok","type":"uid_detected","uid":"04AABBCCDD"}
{"status":"ok","type":"remove_all_except","removed_count":3}
{"status":"ok","type":"wifi_status","connected":true,"message":"..."}
{"status":"ok","type":"net_status","connected":true,"ssid":"...","ip":"...","rssi":-58,"time_synced":true}
{"status":"ok","type":"time","epoch":1783107800,"formatted":"2026-07-03 19:43:20"}
{"status":"ok","type":"ntp_sync","synced":true,"message":"2026-07-03 19:45:00"}
{"status":"ok","type":"import_result","added":1500,"errors":0}
```

---

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `getFirmwareVersion()` prints 0 / PN532 not found | DIP switches not set to SPI, or MISO/MOSI swapped |
| LCD shows nothing / garbled boxes | Wrong I2C address (`0x27` vs `0x3F`); run an I2C scanner sketch to confirm |
| CLI connects but every command times out | Wrong USB port. If your board has two connectors, use the UART-bridge one (CH34x/CP210x), not native USB. Run `list-ports` and pass `--port` explicitly |
| CLI can't find the device at all | Run `python cli.py list-ports` and pass `--port` explicitly |
| Upload fails / board not detected | Some ESP32-S3 boards need the BOOT button held during upload |
| `users.json` keeps resetting to empty | Flash was erased/re-partitioned. LittleFS content doesn't survive partition table changes |
| Every card denied with "No Time Sync" | No Wi-Fi configured (`configure -w ... -p ...`) or network unreachable at boot |
| Cards expire earlier/later than expected | `NTP_GMT_OFFSET_SEC` doesn't match your timezone -- see Section6a |
| No sound from buzzer | Confirm it's a **passive** buzzer wired to GPIO 6 |
| `netstatus` shows `Not connected` after `configure` | Wi-Fi dropped. Re-run `configure` or power-cycle |
| Import fails with "Malformed JSON" | ESP32 rebooting mid-import. Ensure debug logs are removed from Serial (they pollute the JSON protocol) |
| Import shows "Duplicate UID" | The same UID appears twice in your source file. Clean up the JSON/CSV |
| "Database full (max 2000 users)" | Device has reached the 2000-user RAM limit. Use `--clear` to wipe before importing, or reduce the file |

---

## 9. Project Structure

```
RFID_Access_Control/
├── RFID_Access_Control.ino     # setup()/loop() only
├── Config.h                     # pins + constants (baud, Wi-Fi/NTP/buzzer)
├── DatabaseManager.h/.cpp       # LittleFS users.json persistence + uidIndex_ (O(log n) lookup)
├── DisplayManager.h/.cpp        # LCD screen states
├── RFIDManager.h/.cpp           # PN532 hardware-SPI wrapper
├── SerialProtocol.h/.cpp        # newline-JSON framing (streaming for large payloads)
├── NetworkManager.h/.cpp        # Wi-Fi + NTP time sync (NVS-persisted credentials)
├── BuzzerManager.h/.cpp         # passive buzzer tone sequences (GPIO 6)
├── SystemController.h/.cpp      # state machine + import_begin/import_end handling
├── partitions.csv               # custom 16MB layout, 12MB LittleFS cap
├── python_cli/
│   ├── cli.py                   # argparse entry point (13 subcommands)
│   ├── commands.py              # one function per subcommand
│   ├── serial_manager.py        # port auto-detect + boot-wait + retry I/O
│   ├── protocol.py              # JSON message encode/decode + import builders
│   ├── database.py              # typed response parsing
│   ├── utils.py                 # pretty-printing (rich or plain)
│   └── requirements.txt
└── README.md
```

## 10. License

This project is licensed under the MIT License. Do whatever you want with it.
