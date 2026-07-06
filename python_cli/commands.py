"""
commands.py
One function per CLI subcommand. Each function owns its own SerialManager
context (opens the port, does the work, closes it) so the CLI stays a
simple one-shot tool rather than a long-running daemon.
"""

import csv
import re
import signal
import time
from datetime import date
from typing import Optional

import protocol
import utils
from database import (
    parse_user_list, parse_status, parse_net_status,
    require_ok, DatabaseResponseError,
)
from serial_manager import SerialManager, SerialManagerError, list_ports_detailed

UID_RE = re.compile(r"^[0-9A-Fa-f]{8,20}$")


def _today_iso() -> str:
    """ISO-8601 (YYYY-MM-DD) *local* calendar date on the machine running
    the CLI. 'registered' is never entered manually -- it is always
    stamped here, at add-time. The ESP32 must have Config.h's
    NTP_GMT_OFFSET_SEC/NTP_DAYLIGHT_OFFSET_SEC set to match this host's
    timezone, so its expiration math lines up with this date (see README)."""
    return date.today().isoformat()


def _validate_valid_days_or_exit(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        utils.error(f"'{raw}' is not a valid number of days.")
        raise SystemExit(1)
    if value < 0:
        utils.error("valid_days cannot be negative.")
        raise SystemExit(1)
    return value


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


def cmd_find(args) -> None:
    """Find users by name (case-insensitive partial match)."""
    name_query = args.name.strip().lower()
    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_list())
        users = parse_user_list(response)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)

    matches = [u for u in users if name_query in u.name.lower()]
    if not matches:
        utils.error(f"No user found matching '{args.name}'.")
        raise SystemExit(1)

    utils.success(f"Found {len(matches)} match(es):")
    for u in matches:
        print(f"  {u.uid}  {u.name}")


def cmd_add(args) -> None:
    name = args.name or input("Name: ").strip()
    uid_raw = args.uid or input("UID: ").strip()
    uid = _validate_uid_or_exit(uid_raw)

    if not name:
        utils.error("Name cannot be empty.")
        raise SystemExit(1)

    # --- Admin badge handling -------------------------------------------------
    # If the user did NOT pass --valid-days on the command line, we treat the
    # new badge as an ADMIN card: no expiration, no registration date. The
    # firmware stores it with sentinel values (registered="", valid_days=-1)
    # and always grants access for it, regardless of NTP sync state.
    #
    # We deliberately do NOT fall back to an interactive prompt here -- the
    # whole point of the admin shortcut is `./cli.py add --uid X --name Y`
    # with nothing else asked. To create a normal expiring badge
    # interactively (with the prompt), still pass --uid and --name on the
    # command line; --valid-days is the only field that triggers admin mode.
    # --------------------------------------------------------------------------
    if args.valid_days is None:
        registered: Optional[str] = None
        valid_days: Optional[float] = None
        is_admin = True
    else:
        valid_days = _validate_valid_days_or_exit(args.valid_days)
        registered = _today_iso()
        is_admin = False

    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_add(uid, name, registered, valid_days))
        require_ok(response)
        if is_admin:
            utils.success(
                f"Added ADMIN user '{name}' with UID {uid} "
                f"(no expiration, no registration date)."
            )
        else:
            utils.success(
                f"Added user '{name}' with UID {uid} "
                f"(registered {registered}, valid {valid_days} day(s))."
            )
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_remove(args) -> None:
    force = getattr(args, "force", False)
    except_raw = getattr(args, "except_uids", None)

    # --uid, --force, and --except are mutually exclusive -- each names a
    # different scope (one user / everyone / everyone-but-these), so
    # combining them is always ambiguous rather than additive.
    modes_given = sum(bool(x) for x in (args.uid, force, except_raw))
    if modes_given > 1:
        utils.error("Use only one of --uid, --force, or --except at a time.")
        raise SystemExit(1)

    # --force wipes every user from the device.
    if force:
        try:
            with SerialManager(port=args.port) as sm:
                response = sm.request(protocol.build_clear_all())
            require_ok(response)
            utils.success("Removed ALL users from the device database.")
        except (SerialManagerError, DatabaseResponseError) as exc:
            utils.error(str(exc))
            raise SystemExit(1)
        return

    # --except keeps only the listed UID(s) and deletes everyone else.
    if except_raw:
        keep_uids = [_validate_uid_or_exit(u) for u in except_raw.split(",") if u.strip()]
        if not keep_uids:
            utils.error("--except needs at least one UID.")
            raise SystemExit(1)
        try:
            with SerialManager(port=args.port) as sm:
                response = sm.request(protocol.build_remove_all_except(keep_uids))
            require_ok(response)
            removed = response.get("removed_count", "?")
            kept_display = ", ".join(keep_uids)
            utils.success(f"Removed {removed} user(s), kept: {kept_display}.")
        except (SerialManagerError, DatabaseResponseError) as exc:
            utils.error(str(exc))
            raise SystemExit(1)
        return

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


def cmd_netstatus(args) -> None:
    """Show whether the ESP32 is connected to Wi-Fi and to which SSID."""
    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_net_status())
        status = parse_net_status(response)
        utils.print_net_status(status)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_ntp_time(args) -> None:
    """Show the device's current local time (after NTP sync)."""
    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_get_time())
        require_ok(response)
        formatted = response.get("formatted", "?")
        epoch = response.get("epoch", 0)
        utils.success(f"Device time: {formatted} (epoch: {int(epoch)})")
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_ntp_sync(args) -> None:
    """Force an NTP resync on the device."""
    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_ntp_sync())
        if response.get("synced"):
            utils.success(f"NTP synced: {response.get('message', '')}")
        else:
            utils.error(response.get("message", "NTP sync failed."))
            raise SystemExit(1)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_scan(args) -> None:
    infinite = bool(getattr(args, "infinite", False))

    try:
        with SerialManager(port=args.port) as sm:
            # Engage scan mode on the device first.
            ack = sm.request(protocol.build_enter_scan_mode())
            require_ok(ack)

            if infinite:
                utils.info(
                    "Infinite scan mode active -- present cards to the reader."
                )
                utils.info("Press Ctrl+C to stop.")
                count = 0
                # Make Ctrl+C raise KeyboardInterrupt immediately instead of
                # waiting for the current readline() to time out. We do this
                # by installing a SIGINT handler that raises -- pyserial's
                # readline is a blocking syscall that doesn't otherwise
                # react to signals on every platform.
                _install_keyboard_interrupt_handler()

                try:
                    while True:
                        result = sm.wait_for_uid(overall_timeout=3600.0)
                        uid = result.get("uid", "")
                        count += 1
                        utils.success(f"#{count}  Detected UID: {uid}")
                        # The firmware auto-exits scan mode after each card;
                        # re-arm it for the next one. If re-arming fails
                        # (e.g. device unplugged), break out cleanly.
                        try:
                            ack = sm.request(protocol.build_enter_scan_mode())
                            require_ok(ack)
                        except (SerialManagerError, DatabaseResponseError) as exc:
                            utils.error(f"Lost scan mode: {exc}")
                            break
                except KeyboardInterrupt:
                    utils.info("\nScan stopped by user (Ctrl+C).")
                    utils.info(f"Total cards scanned: {count}")
                finally:
                    _restore_default_sigint_handler()
            else:
                utils.info("Scan mode active -- present a card to the reader now...")
                result = sm.wait_for_uid(overall_timeout=args.timeout)
                uid = result.get("uid", "")
                utils.success(f"Detected UID: {uid}")
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_tag_renew(args) -> None:
    """Renew NFC tags: present cards one by one to update their validity.

    Each scanned tag gets registered=today and valid_days set to the
    given value. Only tags already in the device database are renewed.
    LCD shows "RENEWING NFC TAG" during the process.
    """
    valid_days = _validate_valid_days_or_exit(args.valid_days)
    quota_raw = getattr(args, "quota", None)
    quota = None
    if quota_raw is not None and quota_raw.lower() != "none":
        try:
            quota = int(quota_raw)
            if quota <= 0:
                utils.error("Quota must be a positive integer or 'none'.")
                raise SystemExit(1)
        except ValueError:
            utils.error(f"Invalid quota '{quota_raw}'. Use a number or 'none'.")
            raise SystemExit(1)

    try:
        with SerialManager(port=args.port) as sm:
            ack = sm.request(protocol.build_enter_renewal_mode(valid_days))
            require_ok(ack)
            utils.info(f"Renewal mode active (valid_days={valid_days}). Present cards to the reader.")
            utils.info("Press Ctrl+C to stop.")
            _install_keyboard_interrupt_handler()

            count = 0
            try:
                while True:
                    if quota is not None and count >= quota:
                        utils.info(f"Quota reached ({quota}). Stopping.")
                        break
                    # In renewal mode the firmware stays in SCAN_MODE and
                    # sends a renewal_result each time a card is presented.
                    # No need to send enter_scan_mode between cards.
                    try:
                        raw = sm._read_line()
                    except SerialManagerError:
                        continue  # timeout waiting for card, keep looping
                    if not raw:
                        continue
                    try:
                        resp = protocol.decode_message(raw)
                    except Exception:
                        continue  # malformed line, skip
                    if resp.get("type") == "renewal_result":
                        uid = resp.get("uid", "?")
                        name = resp.get("name", "?")
                        reg = resp.get("registered", "?")
                        vd = resp.get("valid_days", "?")
                        count += 1
                        utils.success(f"#{count}  {uid} ({name}) -> registered={reg}, valid_days={vd}")
                    elif resp.get("status") == "error":
                        msg = resp.get("message", "unknown error")
                        utils.error(f"  {msg}")
            except KeyboardInterrupt:
                utils.info("\nRenewal stopped by user (Ctrl+C).")
                utils.info(f"Total tags renewed: {count}")
            finally:
                _restore_default_sigint_handler()
                try:
                    sm.request(protocol.build_exit_renewal_mode())
                except Exception:
                    pass
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_configure_wifi(args) -> None:
    ssid = args.ssid or input("Wi-Fi SSID: ").strip()
    password = args.password if args.password is not None else input("Wi-Fi password: ").strip()

    if not ssid:
        utils.error("SSID cannot be empty.")
        raise SystemExit(1)

    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_configure_wifi(ssid, password))
        if response.get("connected"):
            utils.success(f"Device connected to '{ssid}' and synced its clock.")
        else:
            utils.error(response.get("message", "Device failed to connect."))
            raise SystemExit(1)
    except SerialManagerError as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_import(args) -> None:
    """Import users from a JSON or CSV file into the device database.

    JSON format (matches users.json schema):
        [
          {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
          {"uid": "5AF73581",   "name": "Bob"}
        ]
    Fields 'registered' and 'valid_days' are optional. When omitted the
    badge is created as an admin card (no expiration).

    CSV format (auto-detected by extension):
        uid,name,registered,valid_days
        04AABBCCDD,Alice,2025-01-15,30
        5AF73581,Bob,,
    """
    filepath = args.file
    dry_run = getattr(args, "dry_run", False)
    clear_first = getattr(args, "clear", False)

    today = _today_iso()
    entries = []
    skipped = 0

    if filepath.lower().endswith(".json"):
        entries, skipped = _parse_import_json(filepath, today)
    else:
        entries, skipped = _parse_import_csv(filepath, today)

    if not entries:
        utils.error("No valid entries found in file.")
        raise SystemExit(1)

    utils.info(f"Parsed {len(entries)} user(s) ({skipped} row(s) skipped).")

    if dry_run:
        utils.info("Dry run -- no changes made to the device.")
        for uid, name, reg, vd in entries:
            tag = f"  {uid}  {name}  registered={reg or '(admin)'}  valid_days={vd if vd is not None else '(admin)'}"
            print(tag)
        return

    try:
        with SerialManager(port=args.port) as sm:
            if clear_first:
                utils.info("Clearing existing users...")
                resp = sm.request(protocol.build_clear_all())
                require_ok(resp)
                utils.success("All users removed.")
            else:
                # Check current user count to warn about the 10000-user limit.
                status_resp = sm.request(protocol.build_status())
                current = status_resp.get("user_count", 0)
                if current + len(entries) > 10000:
                    utils.error(
                        f"Device has {current} users, import would add {len(entries)} "
                        f"(total {current + len(entries)}). Max is 10000. "
                        f"Use --clear to wipe first, or reduce the file."
                    )
                    raise SystemExit(1)

            utils.info("Starting batch import...")
            resp = sm.request(protocol.build_import_begin())
            require_ok(resp)

            start = time.time()
            added = 0
            errors = 0
            for uid, name, reg, vd in entries:
                resp = sm.request(protocol.build_add(uid, name, reg, vd))
                if resp.get("status") == "ok":
                    added += 1
                else:
                    errors += 1
                    msg = resp.get("message", "unknown error")
                    utils.error(f"  Failed {uid} ({name}): {msg}")

            resp = sm.request(protocol.build_import_end())
            require_ok(resp)

            elapsed = time.time() - start
            device_added = resp.get("added", added)
            utils.success(
                f"Import complete: {added} added, {errors} errors, "
                f"{device_added} total on device. "
                f"({elapsed:.1f}s)"
            )
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(f"Import failed: {exc}")
        raise SystemExit(1)


def _parse_import_json(filepath: str, today: str):
    """Parse a JSON array of user objects. Returns (entries, skipped)."""
    import json as _json

    try:
        with open(filepath, encoding="utf-8-sig") as f:
            data = _json.load(f)
    except FileNotFoundError:
        utils.error(f"File not found: {filepath}")
        raise SystemExit(1)
    except _json.JSONDecodeError as exc:
        utils.error(f"Invalid JSON: {exc}")
        raise SystemExit(1)

    if not isinstance(data, list):
        utils.error("JSON must be an array of user objects, e.g. [{\"uid\":..., \"name\":...}]")
        raise SystemExit(1)

    entries = []
    skipped = 0
    for i, obj in enumerate(data, start=1):
        if not isinstance(obj, dict):
            skipped += 1
            continue

        uid_raw = str(obj.get("uid", "")).strip()
        name_raw = str(obj.get("name", "")).strip()

        if not uid_raw or not name_raw:
            skipped += 1
            continue

        uid = _normalize_uid(uid_raw)
        if not UID_RE.match(uid):
            utils.error(f"Entry {i}: invalid UID '{uid_raw}' -- skipping.")
            skipped += 1
            continue

        registered_raw = str(obj.get("registered", "")).strip()
        valid_days_raw = obj.get("valid_days")

        if valid_days_raw is not None:
            try:
                valid_days = float(valid_days_raw)
                if valid_days < 0:
                    utils.error(f"Entry {i}: valid_days cannot be negative -- skipping.")
                    skipped += 1
                    continue
                registered = registered_raw or today
            except (TypeError, ValueError):
                utils.error(f"Entry {i}: invalid valid_days '{valid_days_raw}' -- skipping.")
                skipped += 1
                continue
        else:
            valid_days = None
            registered = None

        entries.append((uid, name_raw, registered, valid_days))

    return entries, skipped


def _parse_import_csv(filepath: str, today: str):
    """Parse a CSV file. Returns (entries, skipped)."""
    try:
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                utils.error("CSV file is empty or has no header row.")
                raise SystemExit(1)
            normalized_fields = {fn.strip().lower(): fn for fn in reader.fieldnames}
            if "uid" not in normalized_fields or "name" not in normalized_fields:
                utils.error(
                    f"CSV must have 'uid' and 'name' columns. Found: {reader.fieldnames}"
                )
                raise SystemExit(1)
            rows = list(reader)
    except FileNotFoundError:
        utils.error(f"File not found: {filepath}")
        raise SystemExit(1)
    except csv.Error as exc:
        utils.error(f"CSV parse error: {exc}")
        raise SystemExit(1)

    entries = []
    skipped = 0
    for i, row in enumerate(rows, start=2):
        uid_raw = (row.get(normalized_fields.get("uid", "uid")) or "").strip()
        name_raw = (row.get(normalized_fields.get("name", "name")) or "").strip()

        if not uid_raw or not name_raw:
            skipped += 1
            continue

        uid = _normalize_uid(uid_raw)
        if not UID_RE.match(uid):
            utils.error(f"Row {i}: invalid UID '{uid_raw}' -- skipping.")
            skipped += 1
            continue

        registered_raw = (row.get(normalized_fields.get("registered", "registered")) or "").strip()
        valid_days_raw = (row.get(normalized_fields.get("valid_days", "valid_days")) or "").strip()

        if valid_days_raw:
            try:
                valid_days = float(valid_days_raw)
                if valid_days < 0:
                    utils.error(f"Row {i}: valid_days cannot be negative -- skipping.")
                    skipped += 1
                    continue
                registered = registered_raw or today
            except ValueError:
                utils.error(f"Row {i}: invalid valid_days '{valid_days_raw}' -- skipping.")
                skipped += 1
                continue
        else:
            valid_days = None
            registered = None

        entries.append((uid, name_raw, registered, valid_days))

    return entries, skipped


def cmd_list_ports(args) -> None:
    ports = list_ports_detailed()
    utils.print_port_table(ports)


def cmd_export(args) -> None:
    """Export all users from the device to a JSON file.

    The output is a JSON array matching the users.json schema, ready to
    be re-imported with 'import' later.
    """
    import json as _json

    try:
        with SerialManager(port=args.port) as sm:
            response = sm.request(protocol.build_list())
        users = parse_user_list(response)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)

    data = []
    for u in users:
        entry = {"uid": u.uid, "name": u.name}
        if not u.is_admin:
            entry["registered"] = u.registered
            entry["valid_days"] = u.valid_days
        data.append(entry)

    try:
        with open(args.file, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        utils.error(f"Cannot write file: {exc}")
        raise SystemExit(1)

    utils.success(f"Exported {len(data)} user(s) to {args.file}.")


# -----------------------------------------------------------------------------
# Signal handling helpers for scan --infinite
# -----------------------------------------------------------------------------

_previous_sigint = None


def _install_keyboard_interrupt_handler() -> None:
    """Install a SIGINT handler that raises KeyboardInterrupt, so that
    pyserial's blocking readline() is interrupted promptly on Ctrl+C
    rather than after the timeout expires."""
    global _previous_sigint

    def _handler(signum, frame):
        raise KeyboardInterrupt

    try:
        _previous_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        # signal.signal() can only be called from the main thread -- if
        # we're somehow not in the main thread, Ctrl+C still raises
        # KeyboardInterrupt eventually, just less snappily.
        _previous_sigint = None


def _restore_default_sigint_handler() -> None:
    global _previous_sigint
    if _previous_sigint is not None:
        try:
            signal.signal(signal.SIGINT, _previous_sigint)
        except (ValueError, OSError):
            pass
        _previous_sigint = None
