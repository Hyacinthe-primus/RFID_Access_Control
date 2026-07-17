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

# Common ESP32-S3 (VID, PID) pairs. Add yours here if not auto-detected
# (use `python cli.py list-ports` to check what the OS reports).
KNOWN_ESP32_VID_PID = {
    (0x303A, 0x1001),
    (0x303A, 0x0002),
    (0x10C4, 0xEA60),
    (0x1A86, 0x7523),
    (0x1A86, 0x55D4),
}

DEFAULT_BAUD = 2000000
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_RETRIES = 3


class TransportProfile:
    """Host-side per-request timing (wall-clock, vs firmware's ImportProfiler)."""

    def __init__(self) -> None:
        self.encode_s = 0.0
        self.write_s = 0.0
        self.read_s = 0.0
        self.decode_s = 0.0
        self.request_count = 0

    @property
    def total_s(self) -> float:
        return self.encode_s + self.write_s + self.read_s + self.decode_s

    def summary(self) -> str:
        n = max(self.request_count, 1)
        return (
            f"encode={self.encode_s * 1000:.0f}ms, "
            f"write={self.write_s * 1000:.0f}ms, "
            f"read_wait={self.read_s * 1000:.0f}ms, "
            f"decode={self.decode_s * 1000:.0f}ms "
            f"(total={self.total_s:.1f}s over {self.request_count} requests, "
            f"avg={self.total_s / n * 1000:.1f}ms/request)"
        )


class SerialManagerError(Exception):
    pass


def find_esp32_ports() -> List[str]:
    """Auto-detect every plausible ESP32 port by VID:PID, falling back to a
    description/manufacturer heuristic. VID:PID matches are listed first."""
    candidates: List[str] = []
    fallback: List[str] = []

    for port in list_ports.comports():
        vid_pid = (port.vid, port.pid)
        if vid_pid in KNOWN_ESP32_VID_PID:
            candidates.append(port.device)
            continue

        desc = (port.description or "").lower()
        manuf = (port.manufacturer or "").lower()
        if any(tag in desc or tag in manuf for tag in ("cp210", "ch340", "ch343", "esp32", "usb-serial", "usb serial")):
            fallback.append(port.device)

    return candidates + fallback


def find_esp32_port() -> Optional[str]:
    """Auto-detect a single ESP32 port -- the first plausible candidate."""
    ports = find_esp32_ports()
    return ports[0] if ports else None


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
    """Serial connection with retry-aware request/response semantics."""

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
        self._transport_profile: Optional[TransportProfile] = None  # None = not profiling

    def begin_profiling(self) -> None:
        """Start accumulating host-side per-request timing. Call
        end_profiling() to retrieve and stop."""
        self._transport_profile = TransportProfile()

    def end_profiling(self) -> Optional[TransportProfile]:
        prof = self._transport_profile
        self._transport_profile = None
        return prof

    def __enter__(self) -> "SerialManager":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        try:
            # DTR/RTS low to avoid resetting boards wiring these to EN/GPIO0.
            self._conn = serial.Serial()
            self._conn.port = self.port
            self._conn.baudrate = self.baud
            self._conn.timeout = self.timeout
            self._conn.dtr = False
            self._conn.rts = False
            self._conn.open()
        except serial.SerialException as exc:
            raise SerialManagerError(f"Failed to open {self.port}: {exc}") from exc

        # Give the device time to settle after opening the port.
        time.sleep(1.5)

        self._conn.reset_input_buffer()

        # Wait until the firmware is ready to accept commands.
        self._wait_for_ready()

    def _drain_backlog(self) -> None:
        """Discard stale status replies queued during boot."""
        assert self._conn is not None
        old_timeout = self._conn.timeout
        self._conn.timeout = 0.3
        try:
            while True:
                raw = self._conn.readline()
                if not raw:
                    break  # nothing arrived within 0.3s
        finally:
            self._conn.timeout = old_timeout
        self._conn.reset_input_buffer()

    def _wait_for_ready(self) -> None:
        """Poll 'status' until firmware boots (45s timeout, retries transient
        UART errors -- first boot can trigger a blocking LittleFS.format())."""
        deadline = time.time() + 45.0
        while time.time() < deadline:
            try:
                self._conn.reset_input_buffer()
                self._conn.write(encode_message({"type": "status"}))
                self._conn.flush()
                raw = self._conn.readline()
            except serial.SerialException:
                time.sleep(0.5)
                continue
            if not raw:
                time.sleep(0.5)
                continue
            try:
                decode_message(raw)
                self._drain_backlog()
                return  # firmware is ready, pipe is clean
            except ProtocolError:
                time.sleep(0.5)
                continue
        # If we get here, the device didn't respond in time - proceed
        # anyway and let the actual command fail with a clear error.

    def close(self) -> None:
        if self._conn and self._conn.is_open:
            self._conn.close()

    def ping(self) -> bool:
        """Best-effort liveness check: a short-timeout 'status' round trip.

        Used by the interactive shell after a command error to tell a real
        device disconnect apart from an ordinary validation/usage error,
        without adding any device-health logic to the commands themselves.
        """
        if self._conn is None or not self._conn.is_open:
            return False
        old_timeout = self._conn.timeout
        try:
            self._conn.timeout = 1.0
            self._conn.reset_input_buffer()
            self._conn.write(encode_message({"type": "status"}))
            self._conn.flush()
            raw = self._conn.readline()
            if not raw:
                return False
            decode_message(raw)
            return True
        except (serial.SerialException, ProtocolError):
            return False
        finally:
            self._conn.timeout = old_timeout

    def _read_line(self) -> bytes:
        assert self._conn is not None
        line = self._conn.readline()  # respects self.timeout
        if not line:
            raise SerialManagerError("Timed out waiting for a response from the device.")
        return line

    # Errors that mean "already happened" on retry (lost response, not real failure).
    _RETRY_SAFE_ERRORS = {
        "add": ("duplicate uid",),
        "remove": ("uid not found",),
        "rename": ("uid not found",),
    }

    def request(self, payload: Dict[str, Any], timeout: Optional[float] = None,
                retries: Optional[int] = None) -> Dict[str, Any]:
        """Send one message, return response. Retries on timeout/error;
        treats "already happened" errors as success on retry."""
        assert self._conn is not None, "SerialManager not opened -- use 'with SerialManager(...) as sm:'"

        msg_type = payload.get("type", "")
        safe_errors = self._RETRY_SAFE_ERRORS.get(msg_type, ())
        effective_retries = self.retries if retries is None else retries

        old_timeout = self._conn.timeout
        if timeout is not None:
            self._conn.timeout = timeout
        try:
            last_error: Optional[Exception] = None
            for attempt in range(1, effective_retries + 1):
                try:
                    t0 = time.perf_counter()
                    encoded = encode_message(payload)
                    t1 = time.perf_counter()
                    self._conn.reset_input_buffer()  # cost counted in the write bucket below
                    self._conn.write(encoded)
                    self._conn.flush()
                    t2 = time.perf_counter()
                    raw = self._read_line()
                    t3 = time.perf_counter()
                    response = decode_message(raw)
                    t4 = time.perf_counter()
                    if self._transport_profile is not None:
                        p = self._transport_profile
                        p.encode_s += t1 - t0
                        p.write_s += t2 - t1
                        p.read_s += t3 - t2
                        p.decode_s += t4 - t3
                        p.request_count += 1
                except (SerialManagerError, ProtocolError, serial.SerialException) as exc:
                    last_error = exc
                    time.sleep(0.3)
                    continue

                if (attempt > 1 and response.get("status") == "error" and safe_errors
                        and str(response.get("message", "")).lower() in safe_errors):
                    return {
                        "status": "ok",
                        "note": (
                            f"Treated as success: '{response.get('message')}' on a "
                            f"retry of '{msg_type}' means the first attempt already "
                            f"went through, only its reply was lost."
                        ),
                    }
                return response

            raise SerialManagerError(
                f"No valid response after {effective_retries} attempts: {last_error}"
            )
        finally:
            self._conn.timeout = old_timeout

    def write_raw(self, data: bytes) -> None:
        """Write raw bytes with no framing. Import_bin second half (after ack)."""
        assert self._conn is not None, "SerialManager not opened -- use 'with SerialManager(...) as sm:'"
        self._conn.write(data)
        self._conn.flush()

    def read_raw(self, nbytes: int, timeout: Optional[float] = None) -> bytes:
        """Read exactly nbytes raw bytes. Short read = stream desynced, reconnect."""
        assert self._conn is not None, "SerialManager not opened -- use 'with SerialManager(...) as sm:'"
        old_timeout = self._conn.timeout
        if timeout is not None:
            self._conn.timeout = timeout
        try:
            data = self._conn.read(nbytes)
        finally:
            self._conn.timeout = old_timeout
        if len(data) < nbytes:
            raise SerialManagerError(
                f"export_bin: expected {nbytes} bytes, got {len(data)} "
                f"(transfer stalled/incomplete -- reconnect and retry)"
            )
        return data

    def write_only(self, payload: Dict[str, Any]) -> None:
        """Write without reading response. Pair with one read_only() call, in order."""
        assert self._conn is not None, "SerialManager not opened -- use 'with SerialManager(...) as sm:'"
        encoded = encode_message(payload)
        self._conn.write(encoded)
        self._conn.flush()

    def read_only(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Read one response line. No retry (pipelining primitive)."""
        assert self._conn is not None, "SerialManager not opened -- use 'with SerialManager(...) as sm:'"
        old_timeout = self._conn.timeout
        if timeout is not None:
            self._conn.timeout = timeout
        try:
            raw = self._read_line()
            return decode_message(raw)
        finally:
            self._conn.timeout = old_timeout

    def wait_for_uid(self, overall_timeout: float = 30.0) -> Dict[str, Any]:
        """Block until 'uid_detected' message arrives."""
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
