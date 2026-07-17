#!/usr/bin/env python3
"""RFID Access Control management CLI.

Serial port auto-detected; use --port to override.

Run with no subcommand (``./cli.py``, optionally with ``--port``) to enter
the interactive shell. Run with a subcommand (``./cli.py status``) for the
traditional one-shot scripted behavior -- both modes dispatch to the exact
same command functions in commands.py.
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
        help="Serial port to use (default: auto-detect). With no subcommand, "
             "also used to skip auto-detection when entering the shell.",
    )

    # required=False (not the argparse default) so `./cli.py` with no
    # subcommand is valid -- it drops into the interactive shell instead of
    # erroring out. subcommand_names() below is the single place that
    # introspects this list; nothing else hardcodes it.
    sub = parser.add_subparsers(dest="command", required=False)

    p_list = sub.add_parser("list", help="Show all registered users")
    p_list.set_defaults(func=commands.cmd_list)

    p_find = sub.add_parser(
        "find", help="Find a user by exact UID (O(log n) lookup) or by name (partial match)")
    p_find_group = p_find.add_mutually_exclusive_group(required=True)
    p_find_group.add_argument("--uid", help="Exact UID to look up (O(log n) device-side lookup)")
    p_find_group.add_argument("--name", help="Name to search for (partial match)")
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
        "--no-backup", action="store_true",
        help=(
            "Skip the automatic pre-wipe backup when used with --force. "
            "Saves a full device list() round trip (~20s at 20000 users) "
            "-- only use this if you don't need a recovery copy."
        ),
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

    p_tag_renew = sub.add_parser(
        "tag-renew",
        help="Renew NFC tags: present cards to update their validity",
    )
    p_tag_renew.add_argument("valid_days", type=float, help="New validity period in days")
    p_tag_renew.add_argument(
        "--quota", default=None,
        help="Max tags to renew (number), or 'none' for unlimited (Ctrl+C to stop)",
    )
    p_tag_renew.set_defaults(func=commands.cmd_tag_renew)

    p_list_ports = sub.add_parser("list-ports", help="List all serial ports (for debugging)")
    p_list_ports.set_defaults(func=commands.cmd_list_ports)

    p_configure = sub.add_parser("configure", help="Provision the device's Wi-Fi credentials")
    p_configure.add_argument("-w", "--wifi", dest="ssid", help="Wi-Fi SSID")
    p_configure.add_argument("-p", "--password", dest="password", help="Wi-Fi password")
    p_configure.set_defaults(func=commands.cmd_configure_wifi)

    p_timezone = sub.add_parser(
        "timezone",
        help="Set and persist the device's timezone (no reflash needed)",
    )
    p_timezone.add_argument(
        "--offset", dest="gmt_offset_sec", type=int, required=True,
        help="GMT offset in seconds, e.g. 3600 for UTC+1, -18000 for UTC-5",
    )
    p_timezone.add_argument(
        "--dst", dest="daylight_offset_sec", type=int, default=0,
        help="Daylight-saving offset in seconds, added on top of --offset (default: 0)",
    )
    p_timezone.set_defaults(func=commands.cmd_configure_timezone)

    p_import = sub.add_parser(
        "import",
        help="Import users from a JSON, CSV, or .bin file (batch, single flash write)",
    )
    p_import.add_argument("file", help="Path to JSON, CSV, or .bin file")
    p_import.add_argument(
        "--dry-run", action="store_true",
        help="Parse and validate the CSV without writing to the device.",
    )
    p_import.add_argument(
        "--clear", action="store_true",
        help="Wipe all existing users before importing.",
    )
    p_import.add_argument(
        "--no-backup", action="store_true",
        help=(
            "Skip the automatic pre-wipe backup when used with --clear. "
            "Skips the export_bin round trip and the json/bin prompt entirely "
            "-- only use this if you don't need a recovery copy."
        ),
    )
    p_import.add_argument(
        "--json-transport", action="store_true",
        help="Use the older per-batch JSON transport instead of the raw-binary "
             "one (slower, more device-side CPU/SRAM -- fallback for older "
             "firmware or if the binary path misbehaves on your hardware).",
    )
    p_import.set_defaults(func=commands.cmd_import)

    p_export = sub.add_parser(
        "export",
        help="Export all users from the device to a JSON file",
    )
    p_export.add_argument("file", help="Output file path (e.g. users_backup.json)")
    p_export.add_argument(
        "--json-transport", action="store_true",
        help="Use the older per-user JSON 'list' transport instead of the "
             "raw-binary one (fallback for older firmware or if the binary "
             "path misbehaves on your hardware).",
    )
    p_export.set_defaults(func=commands.cmd_export)

    p_sync = sub.add_parser(
        "sync",
        help="Make the device DB exactly match a local JSON/CSV/.bin file "
             "(merge-diff: only removes/adds/replaces what actually differs)",
    )
    p_sync.add_argument("file", help="Path to JSON, CSV, or .bin file")
    p_sync.add_argument(
        "--dry-run", action="store_true",
        help="Compute and show the remove/add/replace diff without changing the device.",
    )
    p_sync.set_defaults(func=commands.cmd_sync)

    return parser


# Subcommands that need an open device connection. Shared with repl.py so
# the shell can show a friendly "not connected" message instead of letting
# these hit SerialManager's own auto-detect when the user has explicitly
# disconnected.
DEVICE_COMMANDS = frozenset({
    "list", "find", "add", "remove", "rename", "scan", "tag-renew",
    "status", "netstatus", "ntp-time", "ntp-sync", "configure", "timezone", "import", "export", "sync",
})


def subcommand_names(parser: argparse.ArgumentParser) -> set:
    """All subcommand names the parser accepts (e.g. 'list', 'import').

    The shell uses this instead of hardcoding its own copy of the command
    list, so build_parser() stays the single source of truth.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    return set()


def command_help(parser: argparse.ArgumentParser) -> dict:
    """{command_name: one-line help string}, read straight off the parser
    (the same text --help shows) so the shell's help listing can't drift
    out of sync with build_parser()."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return {a.dest: (a.help or "") for a in action._choices_actions}
    return {}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        try:
            from repl import run_repl
        except ImportError:
            utils.error(
                "Interactive mode requires prompt_toolkit and rich. Install "
                "them with: pip install prompt_toolkit rich"
            )
            sys.exit(1)
        run_repl(parser, initial_port=args.port)
        return

    if args.command in DEVICE_COMMANDS and args.port is None:
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
