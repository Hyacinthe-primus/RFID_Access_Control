"""Tests for protocol.py -- wire protocol encode/decode and message builders."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python_cli"))

import protocol


class TestEncodeDecode:
    def test_roundtrip(self):
        msg = {"type": "status"}
        encoded = protocol.encode_message(msg)
        decoded = protocol.decode_message(encoded)
        assert decoded == msg

    def test_newline_terminated(self):
        encoded = protocol.encode_message({"type": "test"})
        assert encoded.endswith(b"\n")

    def test_utf8(self):
        msg = {"type": "add", "name": "Café"}
        encoded = protocol.encode_message(msg)
        decoded = protocol.decode_message(encoded)
        assert decoded["name"] == "Café"

    def test_empty_line_raises(self):
        try:
            protocol.decode_message(b"")
            assert False, "Should have raised ProtocolError"
        except protocol.ProtocolError:
            pass

    def test_malformed_json_raises(self):
        try:
            protocol.decode_message(b"not json\n")
            assert False, "Should have raised ProtocolError"
        except protocol.ProtocolError:
            pass


class TestBuildAdd:
    def test_basic(self):
        msg = protocol.build_add("04AABBCCDD", "Alice", "2025-01-15", 30)
        assert msg["type"] == "add"
        assert msg["uid"] == "04AABBCCDD"
        assert msg["name"] == "Alice"
        assert msg["registered"] == "2025-01-15"
        assert msg["valid_days"] == 30

    def test_admin_omits_optional_fields(self):
        msg = protocol.build_add("04AABBCCDD", "Admin", None, None)
        assert "registered" not in msg
        assert "valid_days" not in msg


class TestBuildBatchAdd:
    def test_structure(self):
        entries = [
            ("04AABBCCDD", "Alice", "2025-01-15", 30),
            ("5AF73581", "Bob", None, None),
        ]
        msg = protocol.build_batch_add(entries)
        assert msg["type"] == "batch_add"
        assert len(msg["users"]) == 2
        assert msg["users"][0]["uid"] == "04AABBCCDD"
        assert msg["users"][1]["uid"] == "5AF73581"
        assert "registered" not in msg["users"][1]


class TestBuilders:
    def test_build_remove(self):
        assert protocol.build_remove("04AABBCCDD") == {"type": "remove", "uid": "04AABBCCDD"}

    def test_build_clear_all(self):
        assert protocol.build_clear_all() == {"type": "clear_all"}

    def test_build_list(self):
        assert protocol.build_list() == {"type": "list"}

    def test_build_find_by_uid(self):
        msg = protocol.build_find_by_uid("04AABBCCDD")
        assert msg == {"type": "find", "uid": "04AABBCCDD"}

    def test_build_find_by_name(self):
        msg = protocol.build_find_by_name("alice")
        assert msg == {"type": "find_name", "query": "alice"}

    def test_build_status(self):
        assert protocol.build_status() == {"type": "status"}

    def test_build_net_status(self):
        assert protocol.build_net_status() == {"type": "net_status"}

    def test_build_configure_wifi(self):
        msg = protocol.build_configure_wifi("MySSID", "pass123")
        assert msg == {"type": "configure_wifi", "ssid": "MySSID", "password": "pass123"}

    def test_build_get_time(self):
        assert protocol.build_get_time() == {"type": "get_time"}

    def test_build_ntp_sync(self):
        assert protocol.build_ntp_sync() == {"type": "ntp_sync"}

    def test_build_configure_timezone(self):
        msg = protocol.build_configure_timezone(3600, 0)
        assert msg == {"type": "configure_timezone", "gmt_offset_sec": 3600, "daylight_offset_sec": 0}

    def test_build_configure_timezone_with_dst(self):
        msg = protocol.build_configure_timezone(3600, 3600)
        assert msg["gmt_offset_sec"] == 3600
        assert msg["daylight_offset_sec"] == 3600

    def test_build_import_begin(self):
        assert protocol.build_import_begin() == {"type": "import_begin"}

    def test_build_import_end(self):
        assert protocol.build_import_end() == {"type": "import_end"}

    def test_build_import_bin(self):
        msg = protocol.build_import_bin(7400)
        assert msg == {"type": "import_bin", "bytes": 7400}

    def test_build_export_bin(self):
        assert protocol.build_export_bin() == {"type": "export_bin"}

    def test_build_sync_begin(self):
        assert protocol.build_sync_begin() == {"type": "sync_begin"}

    def test_build_sync_manifest(self):
        assert protocol.build_sync_manifest() == {"type": "sync_manifest"}

    def test_build_sync_apply(self):
        msg = protocol.build_sync_apply(2, 3, 1)
        assert msg == {"type": "sync_apply", "remove": 2, "add": 3, "replace": 1}

    def test_build_enter_scan_mode(self):
        assert protocol.build_enter_scan_mode() == {"type": "enter_scan_mode"}

    def test_build_enter_renewal_mode(self):
        msg = protocol.build_enter_renewal_mode(30)
        assert msg == {"type": "enter_renewal_mode", "valid_days": 30}

    def test_build_exit_renewal_mode(self):
        assert protocol.build_exit_renewal_mode() == {"type": "exit_renewal_mode"}

    def test_build_remove_all_except(self):
        msg = protocol.build_remove_all_except(["04AABBCCDD", "5AF73581"])
        assert msg == {"type": "remove_all_except", "uids": ["04AABBCCDD", "5AF73581"]}

    def test_build_rename(self):
        msg = protocol.build_rename("04AABBCCDD", "Alice Smith")
        assert msg == {"type": "rename", "uid": "04AABBCCDD", "name": "Alice Smith"}
