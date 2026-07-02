"""
protocol.py
Newline-delimited JSON wire protocol shared with the ESP32 firmware.
Pure functions only -- no serial I/O here (that's serial_manager.py's job).
"""

import json
from typing import Any, Dict


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

def build_add(uid: str, name: str) -> Dict[str, Any]:
    return {"type": "add", "uid": uid, "name": name}


def build_remove(uid: str) -> Dict[str, Any]:
    return {"type": "remove", "uid": uid}


def build_rename(uid: str, name: str) -> Dict[str, Any]:
    return {"type": "rename", "uid": uid, "name": name}


def build_list() -> Dict[str, Any]:
    return {"type": "list"}


def build_enter_scan_mode() -> Dict[str, Any]:
    return {"type": "enter_scan_mode"}


def build_status() -> Dict[str, Any]:
    return {"type": "status"}
