"""One function per CLI subcommand.

Each command opens its own SerialManager when run as a one-shot scripted
call (``./cli.py list``). When run from the interactive shell (repl.py),
the shell attaches its own already-open SerialManager as ``args._sm`` and
every command transparently reuses it instead of opening a new port --
see ``_connection()`` below. Command logic itself never branches on which
mode it's running in.
"""

import csv
import re
import signal
import time
from contextlib import contextmanager
from datetime import date
from typing import Optional

import convert
import protocol
import utils
from database import (
    parse_user_list, parse_status, parse_net_status, parse_find_result,
    parse_batch_add_result, parse_sync_result, require_ok, DatabaseResponseError,
)
from serial_manager import SerialManager, SerialManagerError, list_ports_detailed

UID_RE = re.compile(r"^[0-9A-Fa-f]{8,20}$")


@contextmanager
def _connection(args):
    """Yield a ready SerialManager for this command.

    If the interactive shell has attached a persistent, already-open
    connection (``args._sm``), reuse it and leave it open -- the shell owns
    its lifecycle. Otherwise open a fresh one scoped to this call and close
    it on exit, exactly as every command has always done.
    """
    shared = getattr(args, "_sm", None)
    if shared is not None:
        yield shared
        return
    with SerialManager(port=args.port) as sm:
        yield sm


def _today_iso() -> str:
    """Local ISO date, stamped at add-time. Device timezone must match (see README)."""
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


def _name_fits_device(name: str) -> bool:
    """True if `name` fits in MAX_NAME_LEN UTF-8 bytes on the device.

    Bulk imports (JSON/CSV -> binary transport) encode locally via
    convert.encode_record and never round-trip through the firmware's own
    isValidName() check the way `add` does -- so an over-length name here
    would otherwise be silently truncated mid-character instead of caught.

    Thin wrapper kept for call-site compatibility; the policy itself
    lives in convert.name_fits_device() so the CLI (add/import) and the
    standalone convert.py entry point can't drift apart.
    """
    return convert.name_fits_device(name)


def _auto_backup_before_wipe(sm, reason: str) -> Optional[str]:
    """Backup device DB before a destructive op. Best-effort. Returns path or None."""
    import os

    try:
        raw, count = _export_binary_raw(sm)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(f"Auto-backup skipped -- could not read device database: {exc}")
        return None

    if count == 0:
        return None  # nothing to lose

    os.makedirs("backups", exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    want_json = input(
        f"Backup {count} user(s) as readable JSON? Converted on this machine, "
        f"not the device. [y/N] (N saves the raw .bin as-is, no conversion): "
    ).strip().lower().startswith("y")

    if want_json:
        import json
        data = []
        skipped = 0
        for i in range(count):
            rec = raw[i * convert.RECORD_SIZE:(i + 1) * convert.RECORD_SIZE]
            try:
                data.append(convert.decode_record(rec, f"record {i}"))
            except convert.ConvertError as exc:
                utils.error(f"  Skipping corrupt record {i}: {exc}")
                skipped += 1
        if skipped:
            utils.info(f"  {skipped} record(s) skipped due to corruption.")
        filepath = os.path.join("backups", f"pre_{reason}_{stamp}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            utils.error(f"Auto-backup failed ({exc}) -- proceeding anyway since it was requested.")
            return None
        utils.info(f"Auto-backup: saved {len(data)} user(s) to {filepath} before wipe.")
    else:
        filepath = os.path.join("backups", f"pre_{reason}_{stamp}.bin")
        try:
            with open(filepath, "wb") as f:
                f.write(raw)
        except OSError as exc:
            utils.error(f"Auto-backup failed ({exc}) -- proceeding anyway since it was requested.")
            return None
        utils.info(
            f"Auto-backup: saved {count} user(s) to {filepath} before wipe "
            f"(raw .bin -- re-import directly, or 'python convert.py {filepath} out.json' to read it)."
        )

    return filepath


def cmd_list(args) -> None:
    try:
        with _connection(args) as sm:
            response = sm.request(protocol.build_list())
        users = parse_user_list(response)
        utils.print_user_table(users)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_find(args) -> None:
    """Find user by --uid (O(log n) device-side) or --name (partial match)."""
    uid_raw = getattr(args, "uid", None)

    if uid_raw:
        uid = _validate_uid_or_exit(uid_raw)
        try:
            with _connection(args) as sm:
                response = sm.request(protocol.build_find_by_uid(uid))
            user = parse_find_result(response)
        except (SerialManagerError, DatabaseResponseError) as exc:
            utils.error(str(exc))
            raise SystemExit(1)

        utils.success("Found:")
        print(f"  {user.uid}  {user.name}  registered={user.registered or '(admin)'}  "
              f"valid_days={user.valid_days if not user.is_admin else '(admin)'}")
        return

    name_query = args.name.strip().lower()
    try:
        with _connection(args) as sm:
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
    if not _name_fits_device(name):
        utils.error(f"Name '{name}' exceeds {convert.MAX_NAME_LEN} UTF-8 bytes.")
        raise SystemExit(1)

    # No --valid-days => admin badge (no expiry, sentinel values on device).
    if args.valid_days is None:
        registered: Optional[str] = None
        valid_days: Optional[float] = None
        is_admin = True
    else:
        valid_days = _validate_valid_days_or_exit(args.valid_days)
        registered = _today_iso()
        is_admin = False

    try:
        with _connection(args) as sm:
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
        no_backup = getattr(args, "no_backup", False)
        try:
            with _connection(args) as sm:
                if no_backup:
                    utils.info("Skipping auto-backup (--no-backup).")
                else:
                    _auto_backup_before_wipe(sm, "remove_force")
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
            with _connection(args) as sm:
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
        with _connection(args) as sm:
            response = sm.request(protocol.build_remove(uid), timeout=45.0, retries=1)
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
        with _connection(args) as sm:
            response = sm.request(protocol.build_rename(uid, new_name), retries=1)
        require_ok(response)
        utils.success(f"Renamed UID {uid} to '{new_name}'.")
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_status(args) -> None:
    try:
        with _connection(args) as sm:
            response = sm.request(protocol.build_status())
        status = parse_status(response)
        utils.print_status(status)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_netstatus(args) -> None:
    """Show whether the ESP32 is connected to Wi-Fi and to which SSID."""
    try:
        with _connection(args) as sm:
            response = sm.request(protocol.build_net_status())
        status = parse_net_status(response)
        utils.print_net_status(status)
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_ntp_time(args) -> None:
    """Show the device's current local time (after NTP sync)."""
    try:
        with _connection(args) as sm:
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
        with _connection(args) as sm:
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
        with _connection(args) as sm:
            # Engage scan mode on the device first.
            ack = sm.request(protocol.build_enter_scan_mode())
            require_ok(ack)

            if infinite:
                utils.info(
                    "Infinite scan mode active -- present cards to the reader."
                )
                utils.info("Press Ctrl+C to stop.")
                count = 0
                # So Ctrl+C interrupts the blocking readline() immediately.
                _install_keyboard_interrupt_handler()

                try:
                    while True:
                        result = sm.wait_for_uid(overall_timeout=3600.0)
                        uid = result.get("uid", "")
                        count += 1
                        utils.success(f"#{count}  Detected UID: {uid}")
                        # Firmware auto-exits scan mode after each card; re-arm it.
                        try:
                            ack = sm.request(protocol.build_enter_scan_mode())
                            require_ok(ack)
                        except (SerialManagerError, DatabaseResponseError) as exc:
                            utils.error(f"Lost scan mode: {exc}")
                            break
                except KeyboardInterrupt:
                    _restore_default_sigint_handler()
                    utils.info("\nScan stopped by user (Ctrl+C).")
                    utils.info(f"Total cards scanned: {count}")
                    try:
                        ack = sm.request(protocol.build_exit_scan_mode())
                        require_ok(ack)
                    except (SerialManagerError, DatabaseResponseError) as exc:
                        utils.error(f"Could not return device to idle: {exc}")
                finally:
                    _restore_default_sigint_handler()
            else:
                utils.info("Scan mode active -- present a card to the reader now...")
                _install_keyboard_interrupt_handler()
                try:
                    result = sm.wait_for_uid(overall_timeout=args.timeout)
                    uid = result.get("uid", "")
                    utils.success(f"Detected UID: {uid}")
                except KeyboardInterrupt:
                    _restore_default_sigint_handler()
                    utils.info("\nScan stopped by user (Ctrl+C).")
                    try:
                        ack = sm.request(protocol.build_exit_scan_mode())
                        require_ok(ack)
                    except (SerialManagerError, DatabaseResponseError) as exc:
                        utils.error(f"Could not return device to idle: {exc}")
                except SerialManagerError:
                    _restore_default_sigint_handler()
                    try:
                        ack = sm.request(protocol.build_exit_scan_mode())
                        require_ok(ack)
                    except (SerialManagerError, DatabaseResponseError) as exc:
                        utils.error(f"Could not return device to idle: {exc}")
                    raise
                finally:
                    _restore_default_sigint_handler()
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)


def cmd_tag_renew(args) -> None:
    """Renew tags: present cards to update validity. Only existing DB tags."""
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
        with _connection(args) as sm:
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


def cmd_configure_timezone(args) -> None:
    """Set and persist device timezone. Applied immediately, no reflash."""
    try:
        with _connection(args) as sm:
            response = sm.request(
                protocol.build_configure_timezone(args.gmt_offset_sec, args.daylight_offset_sec)
            )
        if response.get("applied"):
            total = response.get("gmt_offset_sec", args.gmt_offset_sec) + \
                response.get("daylight_offset_sec", args.daylight_offset_sec)
            utils.success(
                f"Timezone set: GMT offset {response.get('gmt_offset_sec')}s, "
                f"DST offset {response.get('daylight_offset_sec')}s "
                f"(total UTC{'+' if total >= 0 else ''}{total // 3600}h). Persisted."
            )
        else:
            utils.error(response.get("message", "Device failed to apply the new timezone."))
            raise SystemExit(1)
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
        with _connection(args) as sm:
            response = sm.request(protocol.build_configure_wifi(ssid, password))
        if response.get("connected"):
            utils.success(f"Device connected to '{ssid}' and synced its clock.")
        else:
            utils.error(response.get("message", "Device failed to connect."))
            raise SystemExit(1)
    except SerialManagerError as exc:
        utils.error(str(exc))
        raise SystemExit(1)


IMPORT_BATCH_SIZE = 100  # sized against firmware's kLineBufCapacity (16384B), ~2.5x headroom


def _send_batch_add(sm, batch):
    """Send one batch_add. On retry, "Duplicate UID" reclassified as success."""
    payload = protocol.build_batch_add(batch)
    try:
        resp = sm.request(payload, retries=1)
    except SerialManagerError:
        resp = sm.request(payload, retries=1)  # one retry; let a 2nd timeout propagate
        added, errors, failed = parse_batch_add_result(resp)
        reclassified = [f for f in failed if f.message.lower() == "duplicate uid"]
        failed = [f for f in failed if f.message.lower() != "duplicate uid"]
        return added + len(reclassified), errors - len(reclassified), failed
    return parse_batch_add_result(resp)


# Maximum number of unacknowledged batch_add requests in flight.
# The device processes one line at a time using a single 16KB input buffer,
# so values >1 can corrupt the serial stream. Keep at 1.
PIPELINE_WINDOW = 1


def _send_batches_pipelined(sm, batches, window=PIPELINE_WINDOW):
    """Send batches with bounded pipeline. Returns (results, confirmed_count)."""
    results = []
    n = len(batches)
    write_idx = 0
    outstanding = 0

    def _write_next():
        nonlocal write_idx, outstanding
        sm.write_only(protocol.build_batch_add(batches[write_idx]))
        write_idx += 1
        outstanding += 1

    confirmed = 0
    try:
        while write_idx < min(window, n):
            _write_next()
        while outstanding > 0:
            resp = sm.read_only(timeout=10.0)
            results.append(parse_batch_add_result(resp))
            outstanding -= 1
            confirmed += 1
            if write_idx < n:
                _write_next()
    except (SerialManagerError, protocol.ProtocolError) as exc:
        utils.error(
            f"  Pipeline desynced after {confirmed}/{n} batches confirmed "
            f"({exc}) -- falling back to per-batch mode for the rest."
        )
        return results, confirmed

    return results, confirmed


def cmd_import(args) -> None:
    """Import users from JSON, CSV, or .bin file. Format auto-detected by extension."""
    filepath = args.file
    dry_run = getattr(args, "dry_run", False)
    clear_first = getattr(args, "clear", False)

    today = _today_iso()
    entries = []
    skipped = 0
    raw_bin_payload = None  # set only for a .bin input -- see _parse_import_bin()

    if filepath.lower().endswith(".json"):
        entries, skipped = _parse_import_json(filepath, today)
    elif filepath.lower().endswith(".bin"):
        entries, skipped, raw_bin_payload = _parse_import_bin(filepath)
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
        with _connection(args) as sm:
            if clear_first:
                no_backup = getattr(args, "no_backup", False)
                if no_backup:
                    utils.info("Skipping auto-backup (--no-backup).")
                else:
                    _auto_backup_before_wipe(sm, "import_clear")
                utils.info("Clearing existing users...")
                resp = sm.request(protocol.build_clear_all())
                require_ok(resp)
                utils.success("All users removed.")
            else:
                # Device's MAX_USERS ceiling (Config.h).
                DEVICE_MAX_USERS = 70000
                status_resp = sm.request(protocol.build_status())
                current = status_resp.get("user_count", 0)
                if current + len(entries) > DEVICE_MAX_USERS:
                    utils.error(
                        f"Device has {current} users, import would add {len(entries)} "
                        f"(total {current + len(entries)}). Max is {DEVICE_MAX_USERS}. "
                        f"Use --clear to wipe first, or reduce the file."
                    )
                    raise SystemExit(1)

                # Skip if file already matches device byte-for-byte (absent on older firmware).
                device_crc = status_resp.get("db_crc32")
                if device_crc is not None:
                    local_crc = convert.compute_canonical_crc32(entries, today)
                    if local_crc == device_crc:
                        utils.success(
                            f"Device already has these exact {len(entries)} user(s) "
                            f"(db_crc32=0x{device_crc:08x} matches) -- nothing to import."
                        )
                        return

            utils.info("Starting batch import...")
            t0 = time.time()
            resp = sm.request(protocol.build_import_begin())
            require_ok(resp)
            utils.info(f"  import_begin ok ({time.time() - t0:.2f}s)")

            start = time.time()
            use_json_transport = getattr(args, "json_transport", False)

            if use_json_transport:
                added, errors, confirmed, n_batches, transport_profile, loop_wall_s = \
                    _send_entries_json_transport(sm, entries, start)
            else:
                added, errors = _send_entries_binary(sm, entries, today, raw_payload=raw_bin_payload)
                confirmed = n_batches = 0  # not meaningful outside the JSON pipeline
                transport_profile = None
                loop_wall_s = 0.0

            # import_end does one blocking flash write of the whole DB -- scale
            # the timeout with entry count instead of retrying a slow write.
            import_end_timeout = max(15.0, 10.0 + len(entries) * 0.01)
            resp = sm.request(protocol.build_import_end(),
                               timeout=import_end_timeout, retries=1)
            require_ok(resp)

            elapsed = time.time() - start
            device_added = resp.get("added", added)
            utils.success(
                f"Import complete: {added} added, {errors} errors, "
                f"{device_added} total on device. "
                f"({elapsed:.1f}s)"
            )
            # Firmware-side timing (ImportProfiler.h) -- absent on older firmware.
            if "json_parse_ms" in resp:
                utils.info(
                    "  Device-side profile: "
                    f"json_parse={resp.get('json_parse_ms')}ms, "
                    f"batch_loop={resp.get('batch_loop_ms')}ms, "
                    f"ack_serialize={resp.get('ack_serialize_ms')}ms, "
                    f"save={resp.get('save_ms')}ms "
                    f"(over {resp.get('batches')} batches, "
                    f"{resp.get('users_profiled')} users)"
                )
            if "save_encode_ms" in resp:
                utils.info(
                    "  save() breakdown: "
                    f"encode={resp.get('save_encode_ms')}ms, "
                    f"write={resp.get('save_write_ms')}ms, "
                    f"finalize={resp.get('save_finalize_ms')}ms"
                )
            if "transport_wait_ms" in resp:
                utils.info(
                    "  Device-side transport wait: "
                    f"{resp.get('transport_wait_ms')}ms total over "
                    f"{resp.get('transport_wait_count')} gaps"
                )
            if use_json_transport:
                utils.info(
                    f"  Pipeline: {confirmed}/{n_batches} batches confirmed "
                    f"without a per-batch round trip (window={PIPELINE_WINDOW})"
                )
                if transport_profile is not None and transport_profile.request_count:
                    utils.info(f"  Host-side request profile (fallback batches only): "
                               f"{transport_profile.summary()}")
                    host_overhead_s = loop_wall_s - transport_profile.total_s
                    utils.info(
                        f"  Host-side loop overhead: {host_overhead_s:.1f}s "
                        f"of {loop_wall_s:.1f}s total loop wall time"
                    )
    except (SerialManagerError, DatabaseResponseError) as exc:
        utils.error(f"Import failed: {exc}")
        raise SystemExit(1)


def _send_entries_binary(sm: "SerialManager", entries, today: str, raw_payload: bytes = None):
    """Binary import: one raw transfer. raw_payload: pre-encoded .bin bytes, or None to encode here."""
    if raw_payload is not None:
        payload = raw_payload
        encode_errors = 0
        utils.info(f"  Sending {len(payload)} bytes read directly from the .bin "
                   f"file as one binary transfer (no re-encoding)...")
    else:
        payload = bytearray()
        encode_errors = 0
        for uid, name, registered, valid_days in entries:
            entry_dict = {"uid": uid, "name": name, "registered": registered, "valid_days": valid_days}
            try:
                payload += convert.encode_record(entry_dict, f"uid {uid}", today)
            except convert.ConvertError as exc:
                utils.error(f"  Skipping {uid}: {exc}")
                encode_errors += 1
        payload = bytes(payload)
        utils.info(f"  Encoded {len(entries) - encode_errors} user(s) into "
                   f"{len(payload)} bytes, sending as one binary transfer...")

    if not payload:
        return 0, encode_errors

    # Stall detector, not a fixed budget; 50KB/s floor is conservative.
    bin_timeout = max(15.0, 5.0 + len(payload) / 50_000.0)

    # retries=1: no safe retry for a raw byte stream mid-transfer.
    resp = sm.request(protocol.build_import_bin(len(payload)),
                       timeout=bin_timeout, retries=1)
    require_ok(resp)

    t_write0 = time.perf_counter()
    sm.write_raw(payload)
    write_s = time.perf_counter() - t_write0

    resp = sm.read_only(timeout=bin_timeout)
    if resp.get("status") != "ok" or resp.get("type") != "import_bin_result":
        raise DatabaseResponseError(
            resp.get("message", f"Unexpected response to import_bin: {resp}")
        )

    added = resp.get("added", 0)
    errors = resp.get("errors", 0) + encode_errors
    utils.info(f"  Binary transfer done in {write_s:.2f}s "
               f"({len(payload) / max(write_s, 1e-6) / 1000:.0f} KB/s)")
    return added, errors


def _send_entries_json_transport(sm: "SerialManager", entries, start: float):
    """Original per-batch JSON pipeline, kept as the --json-transport fallback."""
    added = 0
    errors = 0
    loop_wall_s = 0.0  # total time spent building/sending all batches
    all_batches = [entries[i:i + IMPORT_BATCH_SIZE]
                   for i in range(0, len(entries), IMPORT_BATCH_SIZE)]
    n_batches = len(all_batches)
    processed_users = 0

    # Bulk send via the bounded pipeline first -- see
    # _send_batches_pipelined for why this doesn't need a thread.
    pipeline_t0 = time.perf_counter()
    results, confirmed = _send_batches_pipelined(sm, all_batches)
    loop_wall_s += time.perf_counter() - pipeline_t0

    for batch, (b_added, b_errors, b_failed) in zip(all_batches[:confirmed], results):
        added += b_added
        errors += b_errors
        for f in b_failed:
            utils.error(f"  Failed {f.uid}: {f.message}")
        prev = processed_users
        processed_users += len(batch)
        # "crossed a 500 boundary", not "% 500 == 0" (safe for batches > 500)
        if processed_users // 500 != prev // 500:
            utils.info(f"  ...{processed_users}/{len(entries)} processed "
                       f"({time.time() - start:.1f}s elapsed)")

    # Only covers the per-batch fallback below; pipelined calls aren't timed the same way.
    transport_profile = None
    if confirmed < n_batches:
        remaining = all_batches[confirmed:]
        utils.info(
            f"  Pipeline confirmed {confirmed}/{n_batches} batches; "
            f"resuming remaining {len(remaining)} batch(es) "
            f"({sum(len(b) for b in remaining)} users) in per-batch mode..."
        )
        sm.begin_profiling()
        for offset, batch in enumerate(remaining):
            batch_num = confirmed + offset
            iter_t0 = time.perf_counter()
            t_req = time.time()
            try:
                b_added, b_errors, b_failed = _send_batch_add(sm, batch)
            except SerialManagerError as exc:
                utils.error(
                    f"  Timed out on batch {batch_num + 1}/{n_batches} "
                    f"after {time.time() - t_req:.2f}s: {exc}"
                )
                raise
            added += b_added
            errors += b_errors
            for f in b_failed:
                utils.error(f"  Failed {f.uid}: {f.message}")
            prev = processed_users
            processed_users += len(batch)
            if processed_users // 500 != prev // 500:
                utils.info(f"  ...{processed_users}/{len(entries)} processed "
                           f"({time.time() - start:.1f}s elapsed)")
            loop_wall_s += time.perf_counter() - iter_t0
        transport_profile = sm.end_profiling()

    return added, errors, confirmed, n_batches, transport_profile, loop_wall_s


def _parse_import_bin(filepath: str):
    """Parse users.bin. Returns (entries, skipped, raw_payload for pass-through)."""
    import struct

    try:
        with open(filepath, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        utils.error(f"File not found: {filepath}")
        raise SystemExit(1)

    if len(data) < convert.HEADER_SIZE or data[0:4] != convert.MAGIC:
        utils.error(f"{filepath}: not a valid users.bin file (missing/bad header -- "
                    f"expected magic {convert.MAGIC!r})")
        raise SystemExit(1)
    version = data[4]
    record_size = struct.unpack_from("<H", data, 5)[0]
    if version != convert.VERSION:
        utils.error(f"{filepath}: unsupported format version {version} "
                    f"(this CLI knows version {convert.VERSION})")
        raise SystemExit(1)
    if record_size != convert.RECORD_SIZE:
        utils.error(f"{filepath}: record size {record_size} doesn't match this CLI's "
                    f"compiled-in {convert.RECORD_SIZE} -- likely built against a "
                    f"different Config.h (MAX_NAME_LEN/MAX_UID_HEX_LEN mismatch)")
        raise SystemExit(1)

    payload = data[convert.HEADER_SIZE:]
    n = len(payload) // convert.RECORD_SIZE
    leftover = len(payload) % convert.RECORD_SIZE
    if leftover:
        utils.info(f"Note: {leftover} trailing byte(s) after the last full record "
                   f"in {filepath} (likely a torn write) -- ignored.")
        payload = payload[:n * convert.RECORD_SIZE]

    entries = []
    skipped = 0
    for i in range(n):
        rec = payload[i * convert.RECORD_SIZE:(i + 1) * convert.RECORD_SIZE]
        try:
            e = convert.decode_record(rec, f"record {i}")
        except convert.ConvertError as exc:
            utils.error(f"  Skipping {exc}")
            skipped += 1
            continue
        entries.append((e["uid"], e["name"], e["registered"], e["valid_days"]))

    return entries, skipped, payload


def _parse_import_json(filepath: str, today: str):
    """Parse a JSON array of user objects. Returns (entries, skipped)."""
    import json as _json

    try:
        text, encoding_used = _read_text_with_fallback_encoding(filepath)
    except FileNotFoundError:
        utils.error(f"File not found: {filepath}")
        raise SystemExit(1)
    if encoding_used != "utf-8-sig":
        utils.info(f"Note: {filepath} isn't UTF-8 -- read as {encoding_used}. "
                   f"Re-save as UTF-8 if any names come out garbled below.")

    try:
        data = _json.loads(text)
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

        if not _name_fits_device(name_raw):
            utils.error(f"Entry {i}: name '{name_raw}' exceeds {convert.MAX_NAME_LEN} "
                        f"UTF-8 bytes -- skipping.")
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


def _read_text_with_fallback_encoding(filepath: str):
    """Read text with utf-8-sig → cp1252 → latin-1 fallback. Returns (text, encoding)."""
    with open(filepath, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def _parse_import_csv(filepath: str, today: str):
    """Parse a CSV file. Returns (entries, skipped)."""
    import io

    try:
        text, encoding_used = _read_text_with_fallback_encoding(filepath)
    except FileNotFoundError:
        utils.error(f"File not found: {filepath}")
        raise SystemExit(1)
    if encoding_used != "utf-8-sig":
        utils.info(f"Note: {filepath} isn't UTF-8 -- read as {encoding_used}. "
                   f"Re-save as UTF-8 if any names come out garbled below.")

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
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

        if not _name_fits_device(name_raw):
            utils.error(f"Row {i}: name '{name_raw}' exceeds {convert.MAX_NAME_LEN} "
                        f"UTF-8 bytes -- skipping.")
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
    """Export device DB. .bin = raw binary, anything else = JSON."""
    import json as _json

    use_json_transport = getattr(args, "json_transport", False)
    want_bin = args.file.lower().endswith(".bin")

    try:
        with _connection(args) as sm:
            if want_bin:
                if use_json_transport:
                    response = sm.request(protocol.build_list())
                    users = parse_user_list(response)
                    today = _today_iso()
                    # Sort by UID to match the firmware's sorted invariant --
                    # parse_user_list() sorts by name for display, not UID.
                    users_by_uid = sorted(users, key=lambda u: u.uid)
                    payload = bytearray()
                    for u in users_by_uid:
                        entry = {"uid": u.uid, "name": u.name}
                        if not u.is_admin:
                            entry["registered"] = u.registered
                            entry["valid_days"] = u.valid_days
                        payload += convert.encode_record(entry, f"uid {u.uid}", today)
                    raw = bytes(payload)
                else:
                    raw, _count = _export_binary_raw(sm)
            else:
                if use_json_transport:
                    response = sm.request(protocol.build_list())
                    users = parse_user_list(response)
                    data = []
                    for u in users:
                        entry = {"uid": u.uid, "name": u.name}
                        if not u.is_admin:
                            entry["registered"] = u.registered
                            entry["valid_days"] = u.valid_days
                        data.append(entry)
                else:
                    data = _export_binary(sm)
    except (SerialManagerError, DatabaseResponseError, convert.ConvertError) as exc:
        utils.error(str(exc))
        raise SystemExit(1)

    try:
        if want_bin:
            with open(args.file, "wb") as f:
                f.write(convert.header_bytes())
                f.write(raw)
        else:
            with open(args.file, "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        utils.error(f"Cannot write file: {exc}")
        raise SystemExit(1)

    if want_bin:
        utils.success(f"Exported {len(raw) // convert.RECORD_SIZE} user(s) to {args.file} (raw .bin).")
    else:
        utils.success(f"Exported {len(data)} user(s) to {args.file}.")


def _export_binary_raw(sm: "SerialManager"):
    """Raw export: returns (raw_bytes, count) without decoding."""
    resp = sm.request(protocol.build_export_bin())
    if resp.get("status") != "ok" or resp.get("type") != "export_bin":
        raise DatabaseResponseError(resp.get("message", f"Unexpected response to export_bin: {resp}"))

    total_bytes = int(resp.get("bytes", 0))
    count = int(resp.get("count", 0))
    if total_bytes == 0:
        return b"", 0

    # Scaled the same way import's binary timeout is -- a stall detector,
    # not a fixed budget (see readRawExact()'s comment in SerialProtocol.h).
    timeout = max(15.0, 5.0 + total_bytes / 50_000.0)
    t0 = time.perf_counter()
    raw = sm.read_raw(total_bytes, timeout=timeout)
    read_s = time.perf_counter() - t0
    utils.info(f"  Binary transfer done in {read_s:.2f}s "
               f"({total_bytes / max(read_s, 1e-6) / 1000:.0f} KB/s, {count} user(s))")
    return raw, count


def _export_binary(sm: "SerialManager"):
    """Export via export_bin, decode to list of dicts."""
    raw, count = _export_binary_raw(sm)
    if count == 0:
        return []

    data = []
    skipped = 0
    for i in range(count):
        rec = raw[i * convert.RECORD_SIZE:(i + 1) * convert.RECORD_SIZE]
        try:
            entry = convert.decode_record(rec, f"record {i}")
        except convert.ConvertError as exc:
            utils.error(f"  Skipping corrupt record {i}: {exc}")
            skipped += 1
            continue
        data.append(entry)
    if skipped:
        utils.info(f"  {skipped} record(s) skipped due to corruption.")
    return data


def _dedupe_entries(entries):
    """Explicit last-write-wins dedup on uid.

    A merge-diff needs exactly one entry per uid on each side, but
    json/csv/bin input can all contain duplicate UIDs (import's device-side
    dedup check doesn't apply here since sync never round-trips duplicates
    through the device). Applied uniformly regardless of source format so
    every entry point resolves duplicates the same way. Returns
    (deduped_entries, dup_count).
    """
    by_uid = {}
    order = []
    for uid, name, registered, valid_days in entries:
        norm = uid.upper()
        if norm not in by_uid:
            order.append(norm)
        by_uid[norm] = (norm, name, registered, valid_days)
    dup_count = len(entries) - len(order)
    return [by_uid[u] for u in order], dup_count


def cmd_sync(args) -> None:
    """Make the device DB exactly match a local JSON/CSV/.bin file.

    Unlike `import` (additive, or destructive with --clear), `sync` computes
    a merge-diff (remove/add/replace) against the device's current contents
    and applies only the difference, then verifies the result by comparing
    db_crc32 against the local file's own canonical crc32.
    """
    import struct

    filepath = args.file
    dry_run = getattr(args, "dry_run", False)
    today = _today_iso()

    if filepath.lower().endswith(".json"):
        entries, skipped = _parse_import_json(filepath, today)
    elif filepath.lower().endswith(".bin"):
        entries, skipped, _raw = _parse_import_bin(filepath)
    else:
        entries, skipped = _parse_import_csv(filepath, today)

    if skipped:
        utils.info(f"{skipped} row(s)/record(s) skipped while parsing {filepath}.")

    entries, dup_count = _dedupe_entries(entries)
    if dup_count:
        utils.info(f"{dup_count} duplicate UID(s) in {filepath} -- kept the last occurrence of each.")

    if not entries:
        utils.error(
            "No valid entries found in file -- refusing to sync against an "
            "empty set (use 'remove --force' if you really want to wipe the device)."
        )
        raise SystemExit(1)

    local_crc = convert.compute_canonical_crc32(entries, today)

    try:
        with _connection(args) as sm:
            resp = sm.request(protocol.build_sync_begin())
            if resp.get("status") != "ok" or resp.get("type") != "sync_begin":
                raise DatabaseResponseError(
                    resp.get("message", f"Unexpected response to sync_begin: {resp}")
                )
            device_crc = int(resp["db_crc32"])
            device_count = int(resp["count"])

            if device_crc == local_crc:
                utils.success(
                    f"Device already matches {filepath} exactly "
                    f"({len(entries)} user(s), db_crc32=0x{device_crc:08x}) -- nothing to do."
                )
                return

            utils.info(
                f"Device has {device_count} user(s) (db_crc32=0x{device_crc:08x}); "
                f"local file has {len(entries)} (0x{local_crc:08x}) -- diffing..."
            )

            manifest_resp = sm.request(protocol.build_sync_manifest())
            if manifest_resp.get("status") != "ok" or manifest_resp.get("type") != "sync_manifest":
                raise DatabaseResponseError(
                    manifest_resp.get("message", f"Unexpected response to sync_manifest: {manifest_resp}")
                )
            total_bytes = int(manifest_resp.get("bytes", 0))
            manifest_count = int(manifest_resp.get("count", 0))

            device_crcs = {}
            if total_bytes:
                timeout = max(15.0, 5.0 + total_bytes / 50_000.0)
                t0 = time.perf_counter()
                raw_manifest = sm.read_raw(total_bytes, timeout=timeout)
                read_s = time.perf_counter() - t0
                for i in range(manifest_count):
                    entry = raw_manifest[i * convert.MANIFEST_ENTRY_SIZE:(i + 1) * convert.MANIFEST_ENTRY_SIZE]
                    uid, crc = convert.decode_manifest_entry(entry, f"manifest entry {i}")
                    device_crcs[uid] = crc
                utils.info(f"  Manifest transfer done in {read_s:.2f}s ({manifest_count} user(s))")

            local_records = {}
            local_crcs = {}
            for uid, name, registered, valid_days in entries:
                norm = uid.upper()
                entry_dict = {"uid": norm, "name": name, "registered": registered, "valid_days": valid_days}
                rec = convert.encode_record(entry_dict, f"uid {norm}", today)
                local_records[norm] = rec
                local_crcs[norm] = struct.unpack_from("<I", rec, len(rec) - 4)[0]

            remove_uids = [u for u in device_crcs if u not in local_crcs]
            add_uids = [u for u in local_crcs if u not in device_crcs]
            replace_uids = [u for u in local_crcs
                            if u in device_crcs and local_crcs[u] != device_crcs[u]]
            unchanged = len(local_crcs) - len(add_uids) - len(replace_uids)

            utils.info(
                f"  Diff: {len(remove_uids)} to remove, {len(add_uids)} to add, "
                f"{len(replace_uids)} to replace, {unchanged} unchanged."
            )

            if dry_run:
                utils.info("Dry run -- no changes made to the device.")
                return

            remove_payload = b"".join(convert.encode_uid_entry(u) for u in remove_uids)
            add_payload = b"".join(local_records[u] for u in add_uids)
            replace_payload = b"".join(local_records[u] for u in replace_uids)
            combined = remove_payload + add_payload + replace_payload

            # retries=1: no safe retry for a raw byte stream mid-transfer,
            # same rationale as import_bin.
            apply_timeout = max(15.0, 5.0 + len(combined) / 50_000.0)
            resp = sm.request(
                protocol.build_sync_apply(len(remove_uids), len(add_uids), len(replace_uids)),
                timeout=apply_timeout, retries=1,
            )
            require_ok(resp)

            t0 = time.perf_counter()
            sm.write_raw(combined)
            write_s = time.perf_counter() - t0

            result_resp = sm.read_only(timeout=apply_timeout)
            result = parse_sync_result(result_resp)

            utils.info(f"  Sync transfer done in {write_s:.2f}s "
                       f"({len(combined) / max(write_s, 1e-6) / 1000:.0f} KB/s)")

            if result.errors:
                utils.error(
                    f"{result.errors} op(s) failed on the device -- see the db_crc32 "
                    f"check below for whether that left a real mismatch."
                )

            if result.db_crc32 == local_crc:
                utils.success(
                    f"Sync complete and verified: {result.removed} removed, {result.added} added, "
                    f"{result.replaced} replaced. Device db_crc32=0x{result.db_crc32:08x} matches "
                    f"the local file exactly."
                )
            else:
                utils.error(
                    f"Sync finished but db_crc32 MISMATCH: device=0x{result.db_crc32:08x}, "
                    f"local=0x{local_crc:08x}. Re-run 'sync' to retry."
                )
                raise SystemExit(1)
    except (SerialManagerError, DatabaseResponseError, convert.ConvertError) as exc:
        utils.error(f"Sync failed: {exc}")
        raise SystemExit(1)


# Signal handling helpers for scan --infinite

_previous_sigint = None


def _install_keyboard_interrupt_handler() -> None:
    """SIGINT handler: raises KeyboardInterrupt to interrupt pyserial reads."""
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
