#!/usr/bin/env python3
"""
cli.py
Entry point for the RFID Access Control management CLI.

Usage:
    python cli.py list
    python cli.py add [--uid UID] [--name NAME]
    python cli.py remove [--uid UID]
    python cli.py rename [--uid UID] [--name NAME]
    python cli.py scan [--timeout SECONDS]

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

    p_add = sub.add_parser("add", help="Register a new user")
    p_add.add_argument("--uid")
    p_add.add_argument("--name")
    p_add.set_defaults(func=commands.cmd_add)

    p_remove = sub.add_parser("remove", help="Delete a user by UID")
    p_remove.add_argument("--uid")
    p_remove.set_defaults(func=commands.cmd_remove)

    p_rename = sub.add_parser("rename", help="Rename an existing user")
    p_rename.add_argument("--uid")
    p_rename.add_argument("--name")
    p_rename.set_defaults(func=commands.cmd_rename)

    p_status = sub.add_parser("status", help="Show DB file path and LittleFS storage usage")
    p_status.set_defaults(func=commands.cmd_status)

    p_scan = sub.add_parser("scan", help="Read the next presented card's UID")
    p_scan.add_argument("--timeout", type=float, default=30.0,
                         help="Seconds to wait for a card (default: 30)")
    p_scan.set_defaults(func=commands.cmd_scan)

    p_list_ports = sub.add_parser("list-ports", help="List all serial ports (for debugging)")
    p_list_ports.set_defaults(func=commands.cmd_list_ports)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    commands_needing_port = {"list", "add", "remove", "rename", "scan", "status"}

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
