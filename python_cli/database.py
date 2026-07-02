"""
database.py
The ESP32 is the source of truth for the user database -- this module does
NOT maintain a persistent local copy. It exists to give the rest of the CLI
a typed, validated view of what the device just told us, rather than
passing raw dicts around.
"""

from dataclasses import dataclass
from typing import Any, Dict, List


class DatabaseResponseError(Exception):
    pass


@dataclass(frozen=True)
class User:
    uid: str
    name: str


def parse_user_list(response: Dict[str, Any]) -> List[User]:
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
    if "users" not in response:
        raise DatabaseResponseError("Response missing 'users' field")

    users: List[User] = []
    for entry in response["users"]:
        try:
            users.append(User(uid=entry["uid"], name=entry["name"]))
        except (KeyError, TypeError):
            continue  # skip malformed entries rather than crashing the CLI
    return sorted(users, key=lambda u: u.name.lower())


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


def require_ok(response: Dict[str, Any]) -> None:
    """Raise DatabaseResponseError if the device reported an error."""
    if response.get("status") != "ok":
        raise DatabaseResponseError(response.get("message", "Unknown error"))
