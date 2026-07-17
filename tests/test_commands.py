"""Tests for commands.py -- validation helpers and file parsers.

Covers the layer test_protocol.py / test_database.py / test_convert.py
don't reach: argument validation and CSV/JSON import parsing. Anything
requiring a live SerialManager (the cmd_* functions that talk to the
device) is out of scope here -- these are the pure/file-only pieces.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python_cli"))

import pytest
import commands


class TestNormalizeUid:
    def test_strips_separators_and_uppercases(self):
        assert commands._normalize_uid("04:aa:bb cc") == "04AABBCC"

    def test_already_clean(self):
        assert commands._normalize_uid("04AABBCCDD") == "04AABBCCDD"


class TestValidateUidOrExit:
    def test_valid_uid_returned_normalized(self):
        assert commands._validate_uid_or_exit("04:aa:bb:cc:dd") == "04AABBCCDD"

    def test_too_short_exits(self):
        with pytest.raises(SystemExit):
            commands._validate_uid_or_exit("04AA")

    def test_non_hex_exits(self):
        with pytest.raises(SystemExit):
            commands._validate_uid_or_exit("ZZAABBCCDD")

    def test_too_long_exits(self):
        with pytest.raises(SystemExit):
            commands._validate_uid_or_exit("04AABBCCDD" * 3)


class TestValidateValidDaysOrExit:
    def test_valid_number(self):
        assert commands._validate_valid_days_or_exit("30") == 30.0

    def test_accepts_decimal(self):
        assert commands._validate_valid_days_or_exit("0.5") == 0.5

    def test_negative_exits(self):
        with pytest.raises(SystemExit):
            commands._validate_valid_days_or_exit("-1")

    def test_non_numeric_exits(self):
        with pytest.raises(SystemExit):
            commands._validate_valid_days_or_exit("banana")


class _Args:
    """Minimal stand-in for argparse.Namespace in cmd_remove."""
    def __init__(self, uid=None, force=False, except_uids=None):
        self.uid = uid
        self.force = force
        self.except_uids = except_uids


class TestCmdRemoveModeConflict:
    """--uid / --force / --except are meant to be mutually exclusive.

    argparse itself doesn't enforce this (only --find has a mutex group),
    so cmd_remove checks it manually -- this was previously untested.
    """

    def test_uid_and_force_together_exits(self):
        with pytest.raises(SystemExit):
            commands.cmd_remove(_Args(uid="04AABBCCDD", force=True))

    def test_force_and_except_together_exits(self):
        with pytest.raises(SystemExit):
            commands.cmd_remove(_Args(force=True, except_uids="04AABBCCDD"))

    def test_uid_and_except_together_exits(self):
        with pytest.raises(SystemExit):
            commands.cmd_remove(_Args(uid="04AABBCCDD", except_uids="5AF73581"))

    def test_all_three_together_exits(self):
        with pytest.raises(SystemExit):
            commands.cmd_remove(_Args(uid="04AABBCCDD", force=True, except_uids="5AF73581"))


class TestNameFitsDevice:
    def test_at_limit_ok(self):
        assert commands._name_fits_device("A" * 48) is True

    def test_over_limit_by_multibyte_char_rejected(self):
        assert commands._name_fits_device("A" * 47 + "\u00e9") is False

    def test_plain_ascii_under_limit_ok(self):
        assert commands._name_fits_device("Alice") is True


class TestCmdAddNameLength:
    """Regression: `add` used to only reject an *empty* name locally and
    rely entirely on a device round trip to catch an over-length one.
    It now fails the same way `import` does, without needing hardware."""

    def test_name_at_limit_does_not_raise_on_length_check(self):
        # Just confirms _name_fits_device (the gate cmd_add now calls)
        # accepts a name exactly at the boundary -- doesn't drive the
        # rest of cmd_add, which needs a live connection.
        name = "A" * 48
        assert commands._name_fits_device(name) is True

    def test_name_over_limit_via_multibyte_char_rejected(self):
        name = "A" * 47 + "\u00e9"
        assert commands._name_fits_device(name) is False

    def test_cmd_add_exits_locally_on_over_length_name(self):
        # cmd_add must exit on the length check before ever touching
        # _connection()/SerialManager -- no device needed for this path.
        args = _Args()
        args.name = "A" * 47 + "\u00e9"
        args.uid = "04AABBCCDD"
        args.valid_days = None
        with pytest.raises(SystemExit):
            commands.cmd_add(args)


class TestParseImportJson:
    def _write(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        f.write(content)
        f.close()
        return f.name

    def test_basic_entries(self):
        path = self._write('[{"uid": "04AABBCCDD", "name": "Alice", "valid_days": 30}]')
        try:
            entries, skipped = commands._parse_import_json(path, "2025-07-13")
            assert skipped == 0
            assert entries == [("04AABBCCDD", "Alice", "2025-07-13", 30.0)]
        finally:
            os.unlink(path)

    def test_admin_entry_no_valid_days(self):
        path = self._write('[{"uid": "04AABBCCDD", "name": "Admin"}]')
        try:
            entries, skipped = commands._parse_import_json(path, "2025-07-13")
            assert entries == [("04AABBCCDD", "Admin", None, None)]
        finally:
            os.unlink(path)

    def test_invalid_uid_skipped_not_fatal(self):
        path = self._write(
            '[{"uid": "ZZ", "name": "Bad"}, {"uid": "04AABBCCDD", "name": "Alice", "valid_days": 30}]'
        )
        try:
            entries, skipped = commands._parse_import_json(path, "2025-07-13")
            assert skipped == 1
            assert len(entries) == 1
            assert entries[0][0] == "04AABBCCDD"
        finally:
            os.unlink(path)

    def test_negative_valid_days_skipped(self):
        path = self._write('[{"uid": "04AABBCCDD", "name": "Alice", "valid_days": -5}]')
        try:
            entries, skipped = commands._parse_import_json(path, "2025-07-13")
            assert skipped == 1
            assert entries == []
        finally:
            os.unlink(path)

    def test_name_at_limit_kept(self):
        name = "A" * 48
        path = self._write(json.dumps([{"uid": "04AABBCCDD", "name": name, "valid_days": 30}]))
        try:
            entries, skipped = commands._parse_import_json(path, "2025-07-13")
            assert skipped == 0
            assert entries[0][1] == name
        finally:
            os.unlink(path)

    def test_name_over_limit_via_multibyte_char_skipped_not_corrupted(self):
        # Regression: this exact name used to sail through to
        # convert.encode_record and come back as "...A\ufffd" on the
        # device instead of being rejected. It must now be skipped here,
        # before it ever reaches the encoder.
        name = "A" * 47 + "\u00e9"
        path = self._write(json.dumps([{"uid": "04AABBCCDD", "name": name, "valid_days": 30}]))
        try:
            entries, skipped = commands._parse_import_json(path, "2025-07-13")
            assert skipped == 1
            assert entries == []
        finally:
            os.unlink(path)

    def test_missing_uid_or_name_skipped(self):
        path = self._write('[{"name": "NoUid"}, {"uid": "04AABBCCDD"}]')
        try:
            entries, skipped = commands._parse_import_json(path, "2025-07-13")
            assert skipped == 2
            assert entries == []
        finally:
            os.unlink(path)

    def test_non_array_json_exits(self):
        path = self._write('{"uid": "04AABBCCDD"}')
        try:
            with pytest.raises(SystemExit):
                commands._parse_import_json(path, "2025-07-13")
        finally:
            os.unlink(path)

    def test_missing_file_exits(self):
        with pytest.raises(SystemExit):
            commands._parse_import_json("/nonexistent/path/does_not_exist.json", "2025-07-13")


class TestParseImportCsv:
    def _write(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8", newline="")
        f.write(content)
        f.close()
        return f.name

    def test_basic_rows(self):
        path = self._write("uid,name,valid_days\n04AABBCCDD,Alice,30\n")
        try:
            entries, skipped = commands._parse_import_csv(path, "2025-07-13")
            assert skipped == 0
            assert entries == [("04AABBCCDD", "Alice", "2025-07-13", 30.0)]
        finally:
            os.unlink(path)

    def test_admin_row_blank_valid_days(self):
        path = self._write("uid,name,valid_days\n04AABBCCDD,Admin,\n")
        try:
            entries, skipped = commands._parse_import_csv(path, "2025-07-13")
            assert entries == [("04AABBCCDD", "Admin", None, None)]
        finally:
            os.unlink(path)

    def test_column_names_case_insensitive(self):
        path = self._write("UID,Name\n04AABBCCDD,Alice\n")
        try:
            entries, skipped = commands._parse_import_csv(path, "2025-07-13")
            assert skipped == 0
            assert entries[0][0] == "04AABBCCDD"
        finally:
            os.unlink(path)

    def test_missing_required_columns_exits(self):
        path = self._write("foo,bar\n1,2\n")
        try:
            with pytest.raises(SystemExit):
                commands._parse_import_csv(path, "2025-07-13")
        finally:
            os.unlink(path)

    def test_empty_file_exits(self):
        path = self._write("")
        try:
            with pytest.raises(SystemExit):
                commands._parse_import_csv(path, "2025-07-13")
        finally:
            os.unlink(path)

    def test_invalid_uid_row_skipped(self):
        path = self._write("uid,name\nZZ,Bad\n04AABBCCDD,Alice\n")
        try:
            entries, skipped = commands._parse_import_csv(path, "2025-07-13")
            assert skipped == 1
            assert len(entries) == 1
        finally:
            os.unlink(path)

    def test_name_over_limit_via_multibyte_char_skipped(self):
        name = "A" * 47 + "\u00e9"
        path = self._write(f"uid,name,valid_days\n04AABBCCDD,{name},30\n")
        try:
            entries, skipped = commands._parse_import_csv(path, "2025-07-13")
            assert skipped == 1
            assert entries == []
        finally:
            os.unlink(path)

    def test_missing_file_exits(self):
        with pytest.raises(SystemExit):
            commands._parse_import_csv("/nonexistent/path/does_not_exist.csv", "2025-07-13")


class TestDedupeEntries:
    def test_no_duplicates_passthrough(self):
        entries = [("04AABBCCDD", "Alice", "2025-01-15", 30), ("5AF73581", "Bob", None, None)]
        deduped, dup_count = commands._dedupe_entries(entries)
        assert dup_count == 0
        assert deduped == entries

    def test_duplicate_keeps_last_occurrence(self):
        entries = [
            ("04AABBCCDD", "Alice", "2025-01-15", 30),
            ("04AABBCCDD", "Alice Updated", "2025-02-01", 60),
        ]
        deduped, dup_count = commands._dedupe_entries(entries)
        assert dup_count == 1
        assert len(deduped) == 1
        assert deduped[0] == ("04AABBCCDD", "Alice Updated", "2025-02-01", 60)

    def test_duplicate_uid_case_insensitive(self):
        entries = [
            ("04aabbccdd", "Alice", "2025-01-15", 30),
            ("04AABBCCDD", "Alice Updated", "2025-02-01", 60),
        ]
        deduped, dup_count = commands._dedupe_entries(entries)
        assert dup_count == 1
        assert deduped[0][0] == "04AABBCCDD"

    def test_preserves_first_seen_order(self):
        entries = [
            ("5AF73581", "Bob", None, None),
            ("04AABBCCDD", "Alice", "2025-01-15", 30),
            ("5AF73581", "Bob Updated", None, None),
        ]
        deduped, dup_count = commands._dedupe_entries(entries)
        assert dup_count == 1
        assert [e[0] for e in deduped] == ["5AF73581", "04AABBCCDD"]
        assert deduped[0][1] == "Bob Updated"

    def test_empty_input(self):
        deduped, dup_count = commands._dedupe_entries([])
        assert deduped == []
        assert dup_count == 0
