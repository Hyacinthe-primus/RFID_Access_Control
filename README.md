# RFID Access Control (ESP32-S3)

![C++](https://img.shields.io/badge/Language-C%2B%2B-blue?logo=cplusplus&logoColor=white)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.13-3776AB?style=flat-square&logo=python&logoColor=white)](#)
![ESP32-S3](https://img.shields.io/badge/ESP32-S3-E7352C?logo=espressif&logoColor=white)
![Arduino](https://img.shields.io/badge/Arduino-00979D?logo=arduino&logoColor=white)
![NFC](https://img.shields.io/badge/NFC-PN532-34A853)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.md)

A standalone RFID access controller: ESP32-S3 + PN532 (hardware SPI) + 16x2
I2C LCD + a LittleFS-backed fixed-width binary user database, administered
from a Python CLI (scripted or interactive shell) over serial. Up to 70000
users, PSRAM-backed, with per-record and whole-database CRC32 integrity
checks.

Full docs – wiring, protocol spec, CLI reference, troubleshooting, project
structure live on the **[wiki](https://github.com/Hyacinthe-primus/RFID_Access_Control/wiki)**.

This README only covers getting it cloned, flashed, and running.

## Get the project

Either clone the repository:

```bash
git clone https://github.com/Hyacinthe-primus/RFID_Access_Control.git
```

or download the latest stable release from the [Releases](https://github.com/Hyacinthe-primus/RFID_Access_Control/releases) page.

> **Arduino requirement:** the sketch folder name must match its `.ino`
> filename exactly. This repo is already named `RFID_Access_Control` to
> match [`RFID_Access_Control.ino`](RFID_Access_Control.ino). If you
> download a release ZIP, extract it and ensure the folder is named
> `RFID_Access_Control` before opening it in the Arduino IDE. Likewise, if
> you rename the cloned repository (or download a ZIP that extracts to
> something like `RFID_Access_Control-main`), rename it back or the sketch
> will not compile.

## Firmware

1. Open `RFID_Access_Control.ino` in **Arduino IDE 2.x**.
2. `Tools > Board` -> **ESP32S3 Dev Module**.
3. Install libraries: `Adafruit PN532`, `LiquidCrystal I2C`, `ArduinoJson` (v7.x).
4. Board options: **USB CDC On Boot: Disabled**, **Flash Size: 16MB**,
   **Partition Scheme: Custom** (uses `partitions.csv`), **PSRAM: OPI PSRAM**.
5. `Sketch > Upload`.

Wiring diagram and pinout: see the [wiki](https://github.com/Hyacinthe-primus/RFID_Access_Control/wiki).

## Python CLI

```bash
cd python_cli
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

```bash
python cli.py                 # no subcommand -> interactive shell
python cli.py status          # or run one-shot scripted commands
```

Full command reference, batch import/export, and the interactive shell:
see the [wiki](https://github.com/Hyacinthe-primus/RFID_Access_Control/wiki).

## Tests

```bash
python -m pytest tests/ -v
```

137 tests, run from the repo root, no hardware required.

## License

MIT see [LICENSE.md](LICENSE.md).
