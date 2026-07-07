"""
protocol.py
Newline-delimited JSON wire protocol shared with the ESP32 firmware.
Pure functions only -- no serial I/O here (that's serial_manager.py's job).
"""

import json
from typing import Any, Dict, List, Optional


class ProtocolError(Exception):
    """Raised when a message can't be encoded/decoded per the wire spec."""


def encode_message(payload: Dict[str, Any]) -> bytes:
    """Serialize a dict to a single newline-terminated JSON line."""
    try:
        line = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"Could not encode message: {exc}") from exc
    return (line + "\n").encode("utf-8")


def decode_message(raw_line: bytes) -> Dict[str, Any]:
    """Parse a single line of bytes received from the ESP32."""
    text = raw_line.decode("utf-8", errors="replace").strip()
    if not text:
        raise ProtocolError("Empty line")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Malformed JSON from device: {text!r} ({exc})") from exc


# Message builders (mirror what SystemController::handleSerialMessage_ expects)

def build_add(uid: str, name: str, registered: Optional[str], valid_days: Optional[float]) -> Dict[str, Any]:
    """Build an 'add' message.

    If `registered` or `valid_days` is None the message is sent WITHOUT those
    fields, and the firmware interprets that as "admin NFC card" -- no
    registration date, no expiration, always granted.
    """
    msg: Dict[str, Any] = {
        "type": "add",
        "uid": uid,
        "name": name,
    }
    if registered is not None:
        msg["registered"] = registered
    if valid_days is not None:
        msg["valid_days"] = valid_days
    return msg


def build_remove(uid: str) -> Dict[str, Any]:
    return {"type": "remove", "uid": uid}


def build_clear_all() -> Dict[str, Any]:
    """Wipe every user from the device database."""
    return {"type": "clear_all"}


def build_remove_all_except(keep_uids: List[str]) -> Dict[str, Any]:
    """Delete every user NOT in keep_uids. keep_uids must be non-empty --
    the firmware rejects an empty list rather than treating it as
    'delete everyone' (use build_clear_all() for that instead)."""
    return {"type": "remove_all_except", "uids": keep_uids}


def build_rename(uid: str, name: str) -> Dict[str, Any]:
    return {"type": "rename", "uid": uid, "name": name}


def build_list() -> Dict[str, Any]:
    return {"type": "list"}


def build_enter_scan_mode() -> Dict[str, Any]:
    return {"type": "enter_scan_mode"}


def build_enter_renewal_mode(valid_days: float) -> Dict[str, Any]:
    """Enter renewal mode: each scanned card updates registered=today and valid_days."""
    return {"type": "enter_renewal_mode", "valid_days": valid_days}


def build_exit_renewal_mode() -> Dict[str, Any]:
    """Exit renewal mode and return to idle."""
    return {"type": "exit_renewal_mode"}


def build_status() -> Dict[str, Any]:
    return {"type": "status"}


def build_net_status() -> Dict[str, Any]:
    """Ask the device for its Wi-Fi connection state."""
    return {"type": "net_status"}


def build_configure_wifi(ssid: str, password: str) -> Dict[str, Any]:
    return {"type": "configure_wifi", "ssid": ssid, "password": password}


def build_get_time() -> Dict[str, Any]:
    """Ask the device for its current (local) time after NTP sync."""
    return {"type": "get_time"}


def build_ntp_sync() -> Dict[str, Any]:
    """Force an NTP resync on the device."""
    return {"type": "ntp_sync"}


def build_configure_timezone(gmt_offset_sec: int, daylight_offset_sec: int = 0) -> Dict[str, Any]:
    """Set and persist the device's GMT/DST offset (NVS, survives reboot;
    no reflash needed)."""
    return {
        "type": "configure_timezone",
        "gmt_offset_sec": gmt_offset_sec,
        "daylight_offset_sec": daylight_offset_sec,
    }


def build_import_begin() -> Dict[str, Any]:
    """Start a batch-import session. Subsequent 'add' calls are buffered
    in RAM only; the database is written to flash once at import_end."""
    return {"type": "import_begin"}


def build_import_end() -> Dict[str, Any]:
    """Finalize a batch-import session: persist all buffered users to flash
    in a single write and report how many were added."""
    return {"type": "import_end"}
