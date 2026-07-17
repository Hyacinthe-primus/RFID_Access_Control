"""Tests for database.py -- response parsers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python_cli"))

import pytest
import database


class TestParseUserList:
    def test_basic(self):
        resp = {
            "status": "ok",
            "users": [
                {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
                {"uid": "5AF73581", "name": "Bob", "registered": "", "valid_days": -1},
            ],
        }
        users = database.parse_user_list(resp)
        assert len(users) == 2
        # sorted: admin first, then alpha by name
        assert users[0].is_admin  # Bob (admin)
        assert users[1].uid == "04AABBCCDD"  # Alice

    def test_sorted_by_name(self):
        resp = {
            "status": "ok",
            "users": [
                {"uid": "04AABBCCDD", "name": "Zoe", "registered": "2025-01-15", "valid_days": 30},
                {"uid": "5AF73581", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
            ],
        }
        users = database.parse_user_list(resp)
        assert users[0].name == "Alice"
        assert users[1].name == "Zoe"

    def test_admin_first(self):
        resp = {
            "status": "ok",
            "users": [
                {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
                {"uid": "5AF73581", "name": "Admin", "registered": "", "valid_days": -1},
            ],
        }
        users = database.parse_user_list(resp)
        assert users[0].is_admin
        assert not users[1].is_admin

    def test_error_status_raises(self):
        try:
            database.parse_user_list({"status": "error", "message": "fail"})
            assert False, "Should have raised"
        except database.DatabaseResponseError:
            pass

    def test_malformed_entry_skipped(self):
        resp = {
            "status": "ok",
            "users": [
                {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
                {"broken": True},  # missing uid/name
            ],
        }
        users = database.parse_user_list(resp)
        assert len(users) == 1


class TestParseStatus:
    def test_basic(self):
        resp = {
            "status": "ok",
            "db_path": "/users.bin",
            "fs_total_bytes": 12582912,
            "fs_used_bytes": 1048576,
            "fs_free_bytes": 11534336,
            "user_count": 1500,
        }
        s = database.parse_status(resp)
        assert s.db_path == "/users.bin"
        assert s.user_count == 1500
        assert s.fs_total_bytes == 12582912

    def test_error_raises(self):
        try:
            database.parse_status({"status": "error"})
            assert False
        except database.DatabaseResponseError:
            pass


class TestParseNetStatus:
    def test_connected(self):
        resp = {
            "status": "ok",
            "type": "net_status",
            "connected": True,
            "ssid": "MyWiFi",
            "ip": "192.168.1.100",
            "rssi": -58,
            "time_synced": True,
        }
        ns = database.parse_net_status(resp)
        assert ns.connected is True
        assert ns.ssid == "MyWiFi"
        assert ns.rssi == -58
        assert ns.time_synced is True

    def test_disconnected(self):
        resp = {
            "status": "ok",
            "type": "net_status",
            "connected": False,
            "ssid": "",
            "ip": "",
            "rssi": 0,
            "time_synced": False,
        }
        ns = database.parse_net_status(resp)
        assert ns.connected is False
        assert ns.ssid == ""


class TestParseFindResult:
    def test_found(self):
        resp = {
            "status": "ok",
            "uid": "04AABBCCDD",
            "name": "Alice",
            "registered": "2025-01-15",
            "valid_days": 30,
        }
        user = database.parse_find_result(resp)
        assert user.uid == "04AABBCCDD"
        assert user.name == "Alice"

    def test_not_found(self):
        try:
            database.parse_find_result({"status": "error", "message": "UID not found"})
            assert False
        except database.DatabaseResponseError as e:
            assert "UID not found" in str(e)


class TestParseBatchAddResult:
    def test_all_success(self):
        resp = {"status": "ok", "added": 10, "errors": 0, "failed": []}
        added, errors, failed = database.parse_batch_add_result(resp)
        assert added == 10
        assert errors == 0
        assert len(failed) == 0

    def test_with_failures(self):
        resp = {
            "status": "ok",
            "added": 8,
            "errors": 2,
            "failed": [
                {"uid": "04AABBCCDD", "message": "Duplicate UID"},
                {"uid": "5AF73581", "message": "Invalid name"},
            ],
        }
        added, errors, failed = database.parse_batch_add_result(resp)
        assert added == 8
        assert errors == 2
        assert len(failed) == 2
        assert failed[0].uid == "04AABBCCDD"
        assert failed[0].message == "Duplicate UID"

    def test_call_level_error(self):
        try:
            database.parse_batch_add_result({"status": "error", "message": "Not in import mode"})
            assert False
        except database.DatabaseResponseError:
            pass


class TestExpirationDateStr:
    def test_admin(self):
        user = database.User(uid="04AABBCCDD", name="Admin", registered="", valid_days=-1)
        assert "ADMIN" in database.expiration_date_str(user)

    def test_normal(self):
        user = database.User(uid="04AABBCCDD", name="Alice", registered="2025-01-15", valid_days=30)
        result = database.expiration_date_str(user)
        assert result == "2025-02-14"

    def test_bad_date(self):
        user = database.User(uid="04AABBCCDD", name="Alice", registered="bad", valid_days=30)
        assert database.expiration_date_str(user) == "?"


class TestUser:
    def test_admin_with_empty_registered(self):
        u = database.User(uid="X", name="A", registered="", valid_days=-1)
        assert u.is_admin

    def test_admin_with_negative_valid_days(self):
        u = database.User(uid="X", name="A", registered="2025-01-15", valid_days=-1)
        assert u.is_admin

    def test_normal_user(self):
        u = database.User(uid="X", name="A", registered="2025-01-15", valid_days=30)
        assert not u.is_admin


class TestParseSyncResult:
    def test_basic(self):
        resp = {
            "status": "ok", "type": "sync_result",
            "removed": 2, "added": 3, "replaced": 1, "errors": 0, "db_crc32": 0xDEADBEEF,
        }
        result = database.parse_sync_result(resp)
        assert result.removed == 2
        assert result.added == 3
        assert result.replaced == 1
        assert result.errors == 0
        assert result.db_crc32 == 0xDEADBEEF

    def test_error_status_raises(self):
        resp = {"status": "error", "message": "Failed to persist database"}
        with pytest.raises(database.DatabaseResponseError):
            database.parse_sync_result(resp)

    def test_missing_field_raises(self):
        resp = {"status": "ok", "removed": 0, "added": 0, "replaced": 0}
        with pytest.raises(database.DatabaseResponseError):
            database.parse_sync_result(resp)
