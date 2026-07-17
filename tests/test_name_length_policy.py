#!/usr/bin/env python3
"""Regression tests for the MAX_NAME_LEN (48 UTF-8 byte) policy."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python_cli"))

import pytest
import convert
import commands

OVERLONG_NAME = "A" * 49
FITS_NAME = "A" * 48
MULTIBYTE_OVERLONG = "A" * 47 + "é"


def _write_json(entries):
    f = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    )
    json.dump(entries, f)
    f.close()
    return f.name


class TestSingleSourceOfTruth:
    """Validation helpers share the same implementation."""

    def test_commands_delegates_to_convert(self):
        assert commands._name_fits_device.__doc__ and True
        assert commands._name_fits_device(
            OVERLONG_NAME
        ) == convert.name_fits_device(OVERLONG_NAME)
        assert commands._name_fits_device(
            FITS_NAME
        ) == convert.name_fits_device(FITS_NAME)

    def test_boundary_is_48_bytes_inclusive(self):
        assert convert.name_fits_device(FITS_NAME)
        assert not convert.name_fits_device(OVERLONG_NAME)

    def test_multibyte_boundary_counts_utf8_bytes_not_characters(self):
        assert len(MULTIBYTE_OVERLONG) == 48
        assert not convert.name_fits_device(MULTIBYTE_OVERLONG)


class TestEncodeRecordStaysPermissive:
    """Low-level encoding remains backward compatible."""

    def test_encode_record_does_not_raise_on_overlong_name(self):
        rec = convert.encode_record(
            {"uid": "A245BC12", "name": OVERLONG_NAME},
            "ctx",
            "2026-07-16",
        )
        assert len(rec) == convert.RECORD_SIZE

    def test_encode_record_truncates_rather_than_rejects(self):
        rec = convert.encode_record(
            {"uid": "A245BC12", "name": OVERLONG_NAME},
            "ctx",
            "2026-07-16",
        )
        name_len = rec[1 + convert.UID_BYTES]
        assert name_len <= convert.MAX_NAME_LEN


class TestJsonToBinRejectsOverlongNames:
    """json_to_bin() must reject names that exceed the device limit."""

    def setup_method(self):
        self._tmp_files = []

    def teardown_method(self):
        for path in self._tmp_files:
            if os.path.exists(path):
                os.remove(path)

    def _json_to_bin(self, entries):
        json_path = _write_json(entries)
        self._tmp_files.append(json_path)

        bin_path = json_path + ".bin"
        self._tmp_files.append(bin_path)

        convert.json_to_bin(json_path, bin_path)
        return bin_path

    def test_overlong_name_raises_convert_error(self):
        with pytest.raises(convert.ConvertError):
            self._json_to_bin(
                [{"uid": "A245BC12", "name": OVERLONG_NAME}]
            )

    def test_overlong_multibyte_name_raises_convert_error(self):
        with pytest.raises(convert.ConvertError):
            self._json_to_bin(
                [{"uid": "A245BC12", "name": MULTIBYTE_OVERLONG}]
            )

    def test_no_bin_file_written_when_rejected(self):
        entries = [{"uid": "A245BC12", "name": OVERLONG_NAME}]
        json_path = _write_json(entries)
        self._tmp_files.append(json_path)

        bin_path = json_path + ".bin"
        self._tmp_files.append(bin_path)

        with pytest.raises(convert.ConvertError):
            convert.json_to_bin(json_path, bin_path)

        assert not os.path.exists(bin_path)

    def test_name_at_the_limit_is_accepted(self):
        bin_path = self._json_to_bin(
            [{"uid": "A245BC12", "name": FITS_NAME}]
        )
        assert os.path.exists(bin_path)

    def test_error_message_matches_cli_wording(self):
        with pytest.raises(convert.ConvertError) as excinfo:
            self._json_to_bin(
                [{"uid": "A245BC12", "name": OVERLONG_NAME}]
            )

        assert str(convert.MAX_NAME_LEN) in str(excinfo.value)
        assert "UTF-8 bytes" in str(excinfo.value)


class TestCliJsonImportRejectsOverlongNames:
    """CLI JSON import remains the reference behavior."""

    def setup_method(self):
        self._tmp_files = []

    def teardown_method(self):
        for path in self._tmp_files:
            if os.path.exists(path):
                os.remove(path)

    def test_cli_import_skips_overlong_name_entry(self):
        json_path = _write_json(
            [{"uid": "A245BC12", "name": OVERLONG_NAME}]
        )
        self._tmp_files.append(json_path)

        parsed, skipped = commands._parse_import_json(
            json_path,
            "2026-07-16",
        )

        assert parsed == []
        assert skipped == 1


class TestAllJsonPathsAgreeOnTheSameInput:
    """All JSON entry points must reject the same invalid input."""

    def setup_method(self):
        self._tmp_files = []

    def teardown_method(self):
        for path in self._tmp_files:
            if os.path.exists(path):
                os.remove(path)

    def test_overlong_name_refused_by_every_json_entry_point(self):
        json_path = _write_json(
            [{"uid": "A245BC12", "name": OVERLONG_NAME}]
        )
        self._tmp_files.append(json_path)

        bin_path = json_path + ".bin"
        self._tmp_files.append(bin_path)

        parsed, skipped = commands._parse_import_json(
            json_path,
            "2026-07-16",
        )
        assert parsed == []
        assert skipped == 1

        with pytest.raises(convert.ConvertError):
            convert.json_to_bin(json_path, bin_path)

        assert not os.path.exists(bin_path)
