#!/usr/bin/env python3
"""
cli.py
Entry point for the RFID Access Control management CLI.

Usage:
    python cli.py list                                          # list all users
    python cli.py find --name NAME                              # find users by name (partial match)
    python cli.py add [--uid UID] [--name NAME] [--valid-days DAYS]
    python cli.py remove [--uid UID] [--force] [--except UID[,UID,...]]
    python cli.py rename [--uid UID] [--name NAME]
    python cli.py scan [--timeout SECONDS] [--infinite]
    python cli.py status                                        # DB path + LittleFS storage
    python cli.py netstatus                                     # Wi-Fi state
    python cli.py ntp-time                                      # device current time
    python cli.py ntp-sync                                      # force NTP resync
    python cli.py configure -w SSID -p PASSWORD
    python cli.py import FILE [--dry-run] [--clear]             # batch import from JSON or CSV
    python cli.py export FILE                                   # export DB to JSON
    python cli.py list-ports                                    # list all serial ports

Notes
-----
- `add` without `--valid-days` creates an ADMIN badge: no expiration,
  always granted regardless of NTP sync state.

- `remove --force` wipes every user from the device in one shot.

- `remove --except UID1,UID2` deletes every user EXCEPT the UID(s) listed.
  Mutually exclusive with --uid/--force.

- `scan --infinite` keeps scanning cards until Ctrl+C.

- `netstatus` reports Wi-Fi connection state (SSID, IP, signal).

- `import` reads a JSON or CSV file and sends all users in a single batch
  (one flash write). JSON format matches users.json schema.

- `export` dumps the device DB to a JSON file, useful for backups or
  round-trip with `import`.

- `ntp-time` shows the device's current local time after NTP sync.

- `ntp-sync` forces an NTP resync on the device.

The serial port is auto-detected; pass --port to override.
"""

import argparse
import sys

import commands
import utils
from serial_manager import find_esp32_port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rfid-cli",
        description="Manage the ESP32 RFID Access Control user database.",
    )
    parser.add_argument(
        "--port", default=None,
        help="Serial port to use (default: auto-detect)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Show all registered users")
    p_list.set_defaults(func=commands.cmd_list)

    p_find = sub.add_parser("find", help="Find users by name (partial match)")
    p_find.add_argument("--name", required=True, help="Name to search for")
    p_find.set_defaults(func=commands.cmd_find)

    p_add = sub.add_parser("add", help="Register a new user")
    p_add.add_argument("--uid")
    p_add.add_argument("--name")
    p_add.add_argument(
        "--valid-days", type=float, default=None,
        help=(
            "Days the badge stays valid from today (accepts decimals, e.g. 0.5). "
            "If omitted, the badge is registered as an ADMIN card with no expiration."
        ),
    )
    p_add.set_defaults(func=commands.cmd_add)

    p_remove = sub.add_parser(
        "remove",
        help="Delete a user by UID, wipe every user with --force, or keep "
             "only a set of UIDs with --except",
    )
    p_remove.add_argument("--uid")
    p_remove.add_argument(
        "--force", action="store_true",
        help="Delete ALL users from the device. Use with care.",
    )
    p_remove.add_argument(
        "--except", dest="except_uids", metavar="UID[,UID,...]",
        help=(
            "Delete every user EXCEPT the given UID(s). Comma-separated for "
            "multiple, e.g. --except 04AABBCCDD,5AF73581. Mutually exclusive "
            "with --uid and --force."
        ),
    )
    p_remove.set_defaults(func=commands.cmd_remove)

    p_rename = sub.add_parser("rename", help="Rename an existing user")
    p_rename.add_argument("--uid")
    p_rename.add_argument("--name")
    p_rename.set_defaults(func=commands.cmd_rename)

    p_status = sub.add_parser("status", help="Show DB file path and LittleFS storage usage")
    p_status.set_defaults(func=commands.cmd_status)

    p_netstatus = sub.add_parser(
        "netstatus",
        help="Show Wi-Fi connection state (connected? SSID? IP? signal?)",
    )
    p_netstatus.set_defaults(func=commands.cmd_netstatus)

    p_ntp_time = sub.add_parser(
        "ntp-time",
        help="Show the device's current local time (after NTP sync)",
    )
    p_ntp_time.set_defaults(func=commands.cmd_ntp_time)

    p_ntp_sync = sub.add_parser(
        "ntp-sync",
        help="Force an NTP resync on the device",
    )
    p_ntp_sync.set_defaults(func=commands.cmd_ntp_sync)

    p_scan = sub.add_parser("scan", help="Read the next presented card's UID")
    p_scan.add_argument("--timeout", type=float, default=30.0,
                         help="Seconds to wait for a card (default: 30)")
    p_scan.add_argument(
        "--infinite", action="store_true",
        help="Keep scanning cards until Ctrl+C is pressed.",
    )
    p_scan.set_defaults(func=commands.cmd_scan)

    p_list_ports = sub.add_parser("list-ports", help="List all serial ports (for debugging)")
    p_list_ports.set_defaults(func=commands.cmd_list_ports)

    p_configure = sub.add_parser("configure", help="Provision the device's Wi-Fi credentials")
    p_configure.add_argument("-w", "--wifi", dest="ssid", help="Wi-Fi SSID")
    p_configure.add_argument("-p", "--password", dest="password", help="Wi-Fi password")
    p_configure.set_defaults(func=commands.cmd_configure_wifi)

    p_import = sub.add_parser(
        "import",
        help="Import users from a JSON or CSV file (batch, single flash write)",
    )
    p_import.add_argument("file", help="Path to JSON or CSV file")
    p_import.add_argument(
        "--dry-run", action="store_true",
        help="Parse and validate the CSV without writing to the device.",
    )
    p_import.add_argument(
        "--clear", action="store_true",
        help="Wipe all existing users before importing.",
    )
    p_import.set_defaults(func=commands.cmd_import)

    p_export = sub.add_parser(
        "export",
        help="Export all users from the device to a JSON file",
    )
    p_export.add_argument("file", help="Output file path (e.g. users_backup.json)")
    p_export.set_defaults(func=commands.cmd_export)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    commands_needing_port = {
        "list", "find", "add", "remove", "rename", "scan",
        "status", "netstatus", "ntp-time", "ntp-sync", "configure", "import", "export",
    }

    if args.command in commands_needing_port and args.port is None:
        detected = find_esp32_port()
        if detected is None:
            utils.error(
                "Could not auto-detect the ESP32. Plug it in, or pass --port explicitly."
            )
            sys.exit(1)
        args.port = detected

    args.func(args)


if __name__ == "__main__":
    main()
