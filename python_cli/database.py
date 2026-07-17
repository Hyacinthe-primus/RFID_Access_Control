"""Typed, validated views of device responses. No local persistent copy."""

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


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
        """Admin if no registered date or valid_days=-1."""
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


def parse_find_result(response: Dict[str, Any]) -> User:
    """Parse single-UID find response. Raises on miss."""
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
    try:
        return User(
            uid=response["uid"],
            name=response["name"],
            registered=response.get("registered", "?"),
            valid_days=response.get("valid_days", 0),
        )
    except KeyError as exc:
        raise DatabaseResponseError(f"Response missing field: {exc}")


@dataclass(frozen=True)
class FailedEntry:
    uid: str
    message: str


def parse_batch_add_result(response: Dict[str, Any]) -> Tuple[int, int, List[FailedEntry]]:
    """Parse batch_add response. Raises on call-level error only."""
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
    try:
        added = int(response["added"])
        errors = int(response["errors"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DatabaseResponseError(f"Malformed batch_add_result response: {exc}")

    failed: List[FailedEntry] = []
    for entry in response.get("failed", []):
        try:
            failed.append(FailedEntry(uid=entry["uid"], message=entry.get("message", "unknown error")))
        except (KeyError, TypeError):
            continue  # skip malformed entries rather than crashing the CLI
    return added, errors, failed


def expiration_date_str(user: User) -> str:
    """Human-readable expiration date (display only, device is authority)."""
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


@dataclass(frozen=True)
class SyncResult:
    removed: int
    added: int
    replaced: int
    errors: int
    db_crc32: int


def parse_sync_result(response: Dict[str, Any]) -> SyncResult:
    """Parse the final sync_result response. Raises on call-level error
    (status != ok) -- per-op errors are reported via the `errors` field,
    which does not raise."""
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
    try:
        return SyncResult(
            removed=int(response["removed"]),
            added=int(response["added"]),
            replaced=int(response["replaced"]),
            errors=int(response["errors"]),
            db_crc32=int(response["db_crc32"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DatabaseResponseError(f"Malformed sync_result response: {exc}")
