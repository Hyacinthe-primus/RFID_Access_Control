"""
serial_manager.py
Finds the ESP32's serial port automatically and provides a small,
retry-aware request/response transport for the newline-JSON protocol.
"""

import time
from typing import Any, Dict, List, Optional

import serial
from serial.tools import list_ports

from protocol import encode_message, decode_message, ProtocolError

# (VID, PID) pairs commonly seen on ESP32-S3 boards:
#   0x303A / 0x1001  - Espressif native USB CDC (most ESP32-S3 devkits)
#   0x303A / 0x0002  - Espressif native USB JTAG/serial (bootloader mode)
#   0x10C4 / 0xEA60   - Silicon Labs CP2102/CP2104 (older/alt USB-UART boards)
#   0x1A86 / 0x7523   - WCH CH340 (common on cheap dev boards)
#   0x1A86 / 0x55D4   - WCH CH9102
# You can add your vid/pid here if your board is not detected automatically.
# Use python cli.py list-ports to see what the OS reports for your board.
KNOWN_ESP32_VID_PID = {
    (0x303A, 0x1001),
    (0x1A86, 0x55D3),
    (0x303A, 0x0002),
    (0x10C4, 0xEA60),
    (0x1A86, 0x7523),
    (0x1A86, 0x55D4),
}

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_RETRIES = 3


class SerialManagerError(Exception):
    pass


def find_esp32_port() -> Optional[str]:
    """Return the device path of the first port matching a known ESP32 VID:PID.

    Falls back to a description-based heuristic if no VID:PID match is
    found (some drivers/OS combos don't expose USB IDs cleanly).
    """
    candidates: List[str] = []
    fallback: List[str] = []

    for port in list_ports.comports():
        vid_pid = (port.vid, port.pid)
        if vid_pid in KNOWN_ESP32_VID_PID:
            candidates.append(port.device)
            continue

        desc = (port.description or "").lower()
        manuf = (port.manufacturer or "").lower()
        if any(tag in desc or tag in manuf for tag in ("cp210", "ch340", "esp32", "usb-serial", "usb serial")):
            fallback.append(port.device)

    if candidates:
        return candidates[0]
    if fallback:
        return fallback[0]
    return None


def list_all_ports() -> List[str]:
    return [p.device for p in list_ports.comports()]

def list_ports_detailed() -> List[Dict[str, Any]]:
    """Return every serial port with its VID/PID, Manufacturer and description, for debugging."""
    return [
        {
            "device": p.device,
            "vid": p.vid,
            "pid": p.pid,
            "manufacturer": p.manufacturer or "",
            "description": p.description or "",
        }
        for p in list_ports.comports()
    ]

class SerialManager:
    """Owns the serial.Serial connection and implements request/response
    semantics: send one JSON line, wait for one JSON line back, with
    retry-on-timeout.
    """

    def __init__(self, port: Optional[str] = None, baud: int = DEFAULT_BAUD,
                 timeout: float = DEFAULT_TIMEOUT_S, retries: int = DEFAULT_RETRIES):
        self.port = port or find_esp32_port()
        if not self.port:
            raise SerialManagerError(
                "No ESP32 detected on any serial port. Is it plugged in? "
                "Ports seen: " + (", ".join(list_all_ports()) or "(none)")
            )
        self.baud = baud
        self.timeout = timeout
        self.retries = retries
        self._conn: Optional[serial.Serial] = None

    def __enter__(self) -> "SerialManager":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        try:
            self._conn = serial.Serial(self.port, self.baud, timeout=self.timeout)
        except serial.SerialException as exc:
            raise SerialManagerError(f"Failed to open {self.port}: {exc}") from exc
        # Give the board a moment after DTR toggles (native USB CDC boards
        # sometimes reset on port open).
        time.sleep(1.5)
        self._conn.reset_input_buffer()

    def close(self) -> None:
        if self._conn and self._conn.is_open:
            self._conn.close()

    def _read_line(self) -> bytes:
        assert self._conn is not None
        line = self._conn.readline()  # respects self.timeout
        if not line:
            raise SerialManagerError("Timed out waiting for a response from the device.")
        return line

    def request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send one message, return the decoded response. Retries on
        timeout or malformed-JSON responses up to self.retries times."""
        assert self._conn is not None, "SerialManager not opened -- use 'with SerialManager(...) as sm:'"

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                self._conn.reset_input_buffer()
                self._conn.write(encode_message(payload))
                self._conn.flush()
                raw = self._read_line()
                return decode_message(raw)
            except (SerialManagerError, ProtocolError, serial.SerialException) as exc:
                last_error = exc
                time.sleep(0.3)
                continue

        raise SerialManagerError(
            f"No valid response after {self.retries} attempts: {last_error}"
        )

    def wait_for_uid(self, overall_timeout: float = 30.0) -> Dict[str, Any]:
        """Blocks (up to overall_timeout seconds) reading lines until an
        'uid_detected' message arrives. Used by the 'scan' command after
        enter_scan_mode has been acknowledged."""
        assert self._conn is not None
        deadline = time.time() + overall_timeout
        while time.time() < deadline:
            raw = self._conn.readline()
            if not raw:
                continue
            try:
                msg = decode_message(raw)
            except ProtocolError:
                continue
            if msg.get("type") == "uid_detected":
                return msg
        raise SerialManagerError("Timed out waiting for a card to be presented.")
