"""
commands.py
One function per CLI subcommand. Each function owns its own SerialManager
context (opens the port, does the work, closes it) so the CLI stays a
simple one-shot tool rather than a long-running daemon.
"""

import re

import protocol
import utils
from database import parse_user_list, parse_status, require_ok, DatabaseResponseError
from serial_manager import SerialManager, SerialManagerError, list_ports_detailed

UID_RE = re.compile(r"^[0-9A-Fa-f]{8,20}$")


def _normalize_uid(raw: str) -> str:
    return raw.strip().upper().replace(":", "").replace(" ", "")


def _validate_uid_or_exit(uid: str) -> str:
    norm = _normalize_uid(uid)
    if not UID_RE.match(norm):
        utils.error(f"'{uid}' doesn't look like a valid UID (expected 8-20 hex chars).")
        raise SystemExit(1)
    return norm


def cmd_list(args) -> None:
    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_list())
        users = parse_user_list(response)
        utils.print_user_table(users)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_add(args) -> None:
    name = args.name or input("Name: ").strip()
    uid_raw = args.uid or input("UID: ").strip()
    uid = _validate_uid_or_exit(uid_raw)

    if not name:
        utils.error("Name cannot be empty.")
        raise SystemExit(1)

    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_add(uid, name))
        require_ok(response)
        utils.success(f"Added user '{name}' with UID {uid}.")
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_remove(args) -> None:
    uid_raw = args.uid or input("UID to remove: ").strip()
    uid = _validate_uid_or_exit(uid_raw)

    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_remove(uid))
        require_ok(response)
        utils.success(f"Removed UID {uid}.")
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_rename(args) -> None:
    uid_raw = args.uid or input("UID to rename: ").strip()
    uid = _validate_uid_or_exit(uid_raw)
    new_name = args.name or input("New name: ").strip()

    if not new_name:
        utils.error("Name cannot be empty.")
        raise SystemExit(1)

    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_rename(uid, new_name))
        require_ok(response)
        utils.success(f"Renamed UID {uid} to '{new_name}'.")
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_status(args) -> None:
    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_status())
        status = parse_status(response)
        utils.print_status(status)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_scan(args) -> None:
    try:
        with SerialManager(port=args.port) as sm:
            ack = sm.request(protocol.build_enter_scan_mode())
            require_ok(ack)
            utils.info("Scan mode active -- present a card to the reader now...")
            result = sm.wait_for_uid(overall_timeout=args.timeout)
            uid = result.get("uid", "")
            utils.success(f"Detected UID: {uid}")
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)

def cmd_list_ports(args) -> None:
    ports = list_ports_detailed()
    utils.print_port_table(ports)