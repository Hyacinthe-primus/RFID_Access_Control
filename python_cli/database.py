"""
database.py
The ESP32 is the source of truth for the user database -- this module does
NOT maintain a persistent local copy. It exists to give the rest of the CLI
a typed, validated view of what the device just told us, rather than
passing raw dicts around.
"""

from dataclasses import dataclass
from typing import Any, Dict, List


# Sentinel value used by the firmware to mark "admin" badges that never
# expire. Mirrors DatabaseManager.cpp's ADMIN_VALID_DAYS sentinel.
ADMIN_VALID_DAYS = -1.0
ADMIN_REGISTERED = ""


class DatabaseResponseError(Exception):
    pass


@dataclass(frozen=True)
class User:
    uid: str
    name: str
    registered: str
    valid_days: float

    @property
    def is_admin(self) -> bool:
        """An admin badge has no registration date and never expires.

        The firmware stores admins with `registered=""` and
        `valid_days=-1`; older firmwares that pre-date the admin feature
        always have a real date and a non-negative number of days, so this
        detection is backward-compatible with any existing database.
        """
        return self.registered == ADMIN_REGISTERED or self.valid_days == ADMIN_VALID_DAYS


def parse_user_list(response: Dict[str, Any]) -> List[User]:
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
    if "users" not in response:
        raise DatabaseResponseError("Response missing 'users' field")

    users: List[User] = []
    for entry in response["users"]:
        try:
            users.append(User(
                uid=entry["uid"],
                name=entry["name"],
                registered=entry.get("registered", "?"),
                valid_days=entry.get("valid_days", 0),
            ))
        except (KeyError, TypeError):
            continue  # skip malformed entries rather than crashing the CLI
    return sorted(users, key=lambda u: (not u.is_admin, u.name.lower()))


@dataclass(frozen=True)
class DeviceStatus:
    db_path: str
    fs_total_bytes: int
    fs_used_bytes: int
    fs_free_bytes: int
    user_count: int


def parse_status(response: Dict[str, Any]) -> DeviceStatus:
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
    try:
        return DeviceStatus(
            db_path=response["db_path"],
            fs_total_bytes=response["fs_total_bytes"],
            fs_used_bytes=response["fs_used_bytes"],
            fs_free_bytes=response["fs_free_bytes"],
            user_count=response["user_count"],
        )
    except KeyError as exc:
        raise DatabaseResponseError(f"Response missing field: {exc}")


@dataclass(frozen=True)
class NetworkStatus:
    connected: bool
    ssid: str
    ip: str
    rssi: int               # signal strength in dBm (0 if not connected)
    time_synced: bool


def parse_net_status(response: Dict[str, Any]) -> NetworkStatus:
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
    try:
        return NetworkStatus(
            connected=bool(response.get("connected", False)),
            ssid=response.get("ssid", ""),
            ip=response.get("ip", ""),
            rssi=int(response.get("rssi", 0)),
            time_synced=bool(response.get("time_synced", False)),
        )
    except (KeyError, TypeError) as exc:
        raise DatabaseResponseError(f"Malformed net_status response: {exc}")


def expiration_date_str(user: User) -> str:
    """Best-effort human-readable expiration date for display purposes only
    (the ESP32 is always the authority on whether a badge is actually
    expired, using its own NTP-synced clock)."""
    if user.is_admin:
        return "ADMIN (no expiry)"

    from datetime import date, timedelta

    try:
        y, m, d = (int(p) for p in user.registered.split("-"))
        expires = date(y, m, d) + timedelta(days=user.valid_days)
        return expires.isoformat()
    except (ValueError, TypeError):
        return "?"


def require_ok(response: Dict[str, Any]) -> None:
    """Raise DatabaseResponseError if the device reported an error."""
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
