#!/usr/bin/env python3
"""Standalone JSON <-> BIN converter for the RFID access control DB.
Usage: python convert.py <src> <dst>  (json->bin or bin->json)"""

import sys
import json
import struct
import zlib
import datetime
import re

MAGIC = b"RUD1"
VERSION = 1
UID_BYTES = 10
MAX_UID_HEX_LEN = 20
MIN_UID_HEX_LEN = 8
MAX_NAME_LEN = 48
HEADER_SIZE = 4 + 1 + 2
RECORD_SIZE = 1 + UID_BYTES + 1 + MAX_NAME_LEN + 2 + 8 + 4
ADMIN_DAYS_SENTINEL = 0xFFFF
ADMIN_VALID_DAYS = -1.0


def header_bytes() -> bytes:
    """7-byte .bin file header: MAGIC + VERSION + RECORD_SIZE (on-disk only)."""
    return MAGIC + struct.pack("<B", VERSION) + struct.pack("<H", RECORD_SIZE)

_EPOCH = datetime.date(1970, 1, 1)


class ConvertError(Exception):
    pass


def _normalize_uid(raw: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", raw).upper()


def _validate_uid(uid: str, ctx: str):
    if not (MIN_UID_HEX_LEN <= len(uid) <= MAX_UID_HEX_LEN):
        raise ConvertError(f"{ctx}: UID '{uid}' has invalid length {len(uid)} "
                            f"(expected {MIN_UID_HEX_LEN}-{MAX_UID_HEX_LEN} hex chars)")
    if len(uid) % 2 != 0:
        raise ConvertError(f"{ctx}: UID '{uid}' has an odd number of hex digits")


def name_fits_device(name: str) -> bool:
    """True if `name` fits in MAX_NAME_LEN UTF-8 bytes on the device.

    Single source of truth for the byte-limit policy, shared by every
    user-facing entry point (`add`, `import users.json`/`.csv`, and
    `convert.py input.json output.bin`) so they reject the same names
    the same way. `encode_record()`/`utf8_safe_truncate()` stay
    permissive on purpose -- they're a defensive low-level fallback for
    direct library callers who don't want a raised exception -- but
    every user-facing path should call this first and refuse the input
    outright rather than silently truncating it.
    """
    return len(name.encode("utf-8")) <= MAX_NAME_LEN


def utf8_safe_truncate(name: str, max_bytes: int) -> bytes:
    """Encodes `name` as UTF-8, truncated to at most max_bytes without ever
    splitting a multi-byte character in the middle (which previously left
    a dangling lead byte and decoded back as U+FFFD on readback)."""
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded
    # Back off character-by-character until the slice re-encodes cleanly
    # at or under the byte budget.
    truncated = name
    while truncated and len(truncated.encode("utf-8")) > max_bytes:
        truncated = truncated[:-1]
    return truncated.encode("utf-8")


def _date_to_days(registered) -> int:
    if not registered:
        return ADMIN_DAYS_SENTINEL
    y, m, d = (int(p) for p in registered.split("-"))
    days = (datetime.date(y, m, d) - _EPOCH).days
    return max(0, min(65534, days))


def _days_to_date(days: int):
    if days == ADMIN_DAYS_SENTINEL:
        return None
    return (_EPOCH + datetime.timedelta(days=days)).isoformat()


def encode_record(entry: dict, ctx: str, today: str) -> bytes:
    uid = _normalize_uid(entry["uid"])
    _validate_uid(uid, ctx)
    name = entry.get("name", "")
    if not name or not str(name).strip():
        raise ConvertError(f"{ctx}: empty name for UID {uid}")
    name_bytes = utf8_safe_truncate(str(name), MAX_NAME_LEN)

    # Admin badge = valid_days null/absent (not registered absence).
    valid_days = entry.get("valid_days")
    if valid_days is None:
        reg_days = ADMIN_DAYS_SENTINEL
        vd = ADMIN_VALID_DAYS
    else:
        vd = float(valid_days)
        if vd < 0:
            raise ConvertError(f"{ctx}: negative valid_days for UID {uid}")
        registered = entry.get("registered") or today
        reg_days = _date_to_days(registered)

    uid_bytes = bytes.fromhex(uid).ljust(UID_BYTES, b"\x00")
    rec = bytearray()
    rec += struct.pack("<B", len(uid) // 2)
    rec += uid_bytes
    rec += struct.pack("<B", len(name_bytes))
    rec += name_bytes.ljust(MAX_NAME_LEN, b"\x00")
    rec += struct.pack("<H", reg_days)
    rec += struct.pack("<d", vd)
    crc = zlib.crc32(bytes(rec)) & 0xFFFFFFFF
    rec += struct.pack("<I", crc)
    assert len(rec) == RECORD_SIZE
    return bytes(rec)


def decode_record(rec: bytes, ctx: str) -> dict:
    if len(rec) != RECORD_SIZE:
        raise ConvertError(f"{ctx}: truncated record ({len(rec)} of {RECORD_SIZE} bytes)")
    stored_crc = struct.unpack_from("<I", rec, RECORD_SIZE - 4)[0]
    actual_crc = zlib.crc32(rec[:RECORD_SIZE - 4]) & 0xFFFFFFFF
    if stored_crc != actual_crc:
        raise ConvertError(f"{ctx}: CRC32 mismatch (record corrupt) -- "
                            f"stored 0x{stored_crc:08x}, computed 0x{actual_crc:08x}")

    off = 0
    uid_len = rec[off]; off += 1
    uid_bytes = rec[off:off + UID_BYTES]; off += UID_BYTES
    name_len = rec[off]; off += 1
    name_bytes = rec[off:off + MAX_NAME_LEN]; off += MAX_NAME_LEN
    reg_days = struct.unpack_from("<H", rec, off)[0]; off += 2
    valid_days = struct.unpack_from("<d", rec, off)[0]; off += 8

    if uid_len > UID_BYTES or name_len > MAX_NAME_LEN:
        raise ConvertError(f"{ctx}: implausible uidLen/nameLen ({uid_len}/{name_len})")

    uid = uid_bytes[:uid_len].hex().upper()
    name = name_bytes[:name_len].decode("utf-8", errors="replace")
    is_admin = (reg_days == ADMIN_DAYS_SENTINEL) or (valid_days == ADMIN_VALID_DAYS)

    entry = {"uid": uid, "name": name}
    if is_admin:
        entry["registered"] = None
        entry["valid_days"] = None
    else:
        entry["registered"] = _days_to_date(reg_days)
        entry["valid_days"] = valid_days
    return entry


def compute_canonical_crc32(entries, today: str) -> int:
    """CRC32 over every encoded record's payload, sorted. Must match
    DatabaseManager::computeCrc32().

    IMPORTANT: each encoded record ends with its own 4-byte per-record
    CRC32 (see encode_record()), used only for single-record corruption
    detection. That trailing CRC is EXCLUDED here. Including it would
    make every record a CRC32 "codeword" (data followed by its own
    CRC), and chaining CRC32 across codewords collapses to a constant
    that depends only on the number of records, not their content --
    silently defeating this exact aggregate check. Hash only
    rec[:-4] (uidLen+uid+nameLen+name+regDays+validDays) per record.
    """
    encoded = []
    for uid, name, registered, valid_days in entries:
        entry_dict = {"uid": uid, "name": name, "registered": registered, "valid_days": valid_days}
        rec = encode_record(entry_dict, f"uid {uid}", today)
        encoded.append((_normalize_uid(uid), rec))
    encoded.sort(key=lambda r: r[0])

    crc = 0
    for _, rec in encoded:
        crc = zlib.crc32(rec[:-4], crc) & 0xFFFFFFFF
    return crc


# -- sync protocol wire helpers --
# Manifest entry = uidLen(1) + uidBytes(UID_BYTES) + per-record CRC32(4).
# Mirrors DatabaseManager::encodeManifestEntryAt() -- reuses the same
# trailing CRC32 already embedded in encode_record()'s output, so a
# record's manifest entry is always its own uid prefix + its own trailing
# CRC, never independently derived.
MANIFEST_ENTRY_SIZE = 1 + UID_BYTES + 4

# Bare-uid entry = uidLen(1) + uidBytes(UID_BYTES) -- the sync protocol's
# "remove" list wire format. Mirrors DatabaseManager::uidEntrySize().
UID_ENTRY_SIZE = 1 + UID_BYTES


def encode_uid_entry(uid: str) -> bytes:
    """Encodes a bare UID for the sync protocol's remove list."""
    norm = _normalize_uid(uid)
    _validate_uid(norm, f"uid {norm}")
    uid_bytes = bytes.fromhex(norm).ljust(UID_BYTES, b"\x00")
    return struct.pack("<B", len(norm) // 2) + uid_bytes


def decode_manifest_entry(entry: bytes, ctx: str):
    """Returns (uid, record_crc32) for one manifest entry."""
    if len(entry) != MANIFEST_ENTRY_SIZE:
        raise ConvertError(f"{ctx}: truncated manifest entry ({len(entry)} of {MANIFEST_ENTRY_SIZE} bytes)")
    uid_len = entry[0]
    if uid_len > UID_BYTES:
        raise ConvertError(f"{ctx}: implausible uidLen ({uid_len})")
    uid_bytes = entry[1:1 + UID_BYTES]
    crc = struct.unpack_from("<I", entry, 1 + UID_BYTES)[0]
    uid = uid_bytes[:uid_len].hex().upper()
    return uid, crc


def json_to_bin(json_path: str, bin_path: str):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ConvertError("Input JSON must be a top-level array of user objects")

    today = datetime.date.today().isoformat()
    seen = set()
    records = []
    for i, entry in enumerate(data):
        ctx = f"entry {i}"
        if "uid" not in entry:
            raise ConvertError(f"{ctx}: missing 'uid'")
        uid = _normalize_uid(entry["uid"])
        if uid in seen:
            raise ConvertError(f"{ctx}: duplicate UID {uid}")
        seen.add(uid)

        # Enforce the same byte-limit policy as the CLI's `add`/`import`
        # paths (see name_fits_device()) *before* encode_record(), whose
        # own truncation is a permissive fallback for direct library
        # callers, not the user-facing policy. Without this check here,
        # `convert.py a.json a.bin` would silently produce a .bin with a
        # truncated name for input the CLI's own `import a.json` would
        # reject outright -- the same content, two different outcomes.
        name = str(entry.get("name", ""))
        if not name_fits_device(name):
            raise ConvertError(f"{ctx}: name '{name}' exceeds {MAX_NAME_LEN} "
                                f"UTF-8 bytes")

        records.append((uid, encode_record(entry, ctx, today)))

    # Sort by uid (matches firmware's sorted invariant).
    records.sort(key=lambda r: r[0])

    with open(bin_path, "wb") as f:
        f.write(header_bytes())
        for _, rec in records:
            f.write(rec)

    print(f"Wrote {len(records)} user(s) to {bin_path} "
          f"({HEADER_SIZE + len(records) * RECORD_SIZE} bytes)")


def bin_to_json(bin_path: str, json_path: str):
    with open(bin_path, "rb") as f:
        data = f.read()

    if len(data) < HEADER_SIZE:
        raise ConvertError(f"{bin_path}: file too small to contain a header")
    if data[0:4] != MAGIC:
        raise ConvertError(f"{bin_path}: bad magic bytes (not a users.bin file, "
                            f"or wrong endianness/format)")
    version = data[4]
    record_size = struct.unpack_from("<H", data, 5)[0]
    if version != VERSION:
        raise ConvertError(f"{bin_path}: unsupported version {version} (this tool knows version {VERSION})")
    if record_size != RECORD_SIZE:
        raise ConvertError(f"{bin_path}: record size {record_size} doesn't match this tool's "
                            f"compiled-in {RECORD_SIZE} -- likely a MAX_NAME_LEN/MAX_UID_HEX_LEN "
                            f"mismatch between this script and Config.h")

    payload = data[HEADER_SIZE:]
    n = len(payload) // RECORD_SIZE
    leftover = len(payload) % RECORD_SIZE
    if leftover:
        print(f"Warning: {leftover} trailing bytes after the last full record "
              f"(likely a torn write) -- ignored, same as the firmware's loader.")

    entries = []
    skipped = 0
    for i in range(n):
        rec = payload[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]
        try:
            entries.append(decode_record(rec, f"record {i}"))
        except ConvertError as e:
            print(f"Warning: skipping {e}")
            skipped += 1

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(entries)} user(s) to {json_path}"
          + (f" ({skipped} record(s) skipped due to corruption)" if skipped else ""))


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    src, dst = sys.argv[1], sys.argv[2]
    src_is_json = src.lower().endswith(".json")
    dst_is_json = dst.lower().endswith(".json")

    try:
        if src_is_json and not dst_is_json:
            json_to_bin(src, dst)
        elif not src_is_json and dst_is_json:
            bin_to_json(src, dst)
        else:
            print("Error: exactly one of the two paths must end in .json and the "
                  "other must be the .bin file (json->bin or bin->json).")
            sys.exit(1)
    except (ConvertError, FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
