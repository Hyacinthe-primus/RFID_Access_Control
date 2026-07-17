"""Tests for convert.py -- JSON <-> BIN codec."""

import os
import sys
import struct
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python_cli"))

import pytest
import convert


class TestConstants:
    def test_record_size(self):
        assert convert.RECORD_SIZE == 74

    def test_header_size(self):
        assert convert.HEADER_SIZE == 7

    def test_magic(self):
        assert convert.MAGIC == b"RUD1"

    def test_admin_sentinels(self):
        assert convert.ADMIN_DAYS_SENTINEL == 0xFFFF
        assert convert.ADMIN_VALID_DAYS == -1.0


class TestHeaderBytes:
    def test_length(self):
        h = convert.header_bytes()
        assert len(h) == 7

    def test_magic(self):
        h = convert.header_bytes()
        assert h[:4] == b"RUD1"

    def test_version(self):
        h = convert.header_bytes()
        assert h[4] == 1

    def test_record_size_le(self):
        h = convert.header_bytes()
        rs = struct.unpack_from("<H", h, 5)[0]
        assert rs == convert.RECORD_SIZE


class TestEncodeRecord:
    def test_basic_record(self):
        entry = {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        assert len(rec) == convert.RECORD_SIZE

    def test_uid_bytes(self):
        entry = {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        uid_len = rec[0]
        assert uid_len == 5  # 10 hex chars / 2
        uid_bytes = rec[1:11]
        assert uid_bytes[:5] == bytes.fromhex("04AABBCCDD")
        assert uid_bytes[5:] == b"\x00\x00\x00\x00\x00"

    def test_name_bytes(self):
        entry = {"uid": "04AABBCCDD", "name": "Bob", "registered": "2025-01-15", "valid_days": 30}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        name_len = rec[11]
        assert name_len == 3
        name_bytes = rec[12:12 + convert.MAX_NAME_LEN]
        assert name_bytes[:3] == b"Bob"
        assert name_bytes[3:] == b"\x00" * (convert.MAX_NAME_LEN - 3)

    def test_admin_record(self):
        entry = {"uid": "04AABBCCDD", "name": "Admin"}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        # offset: uidLen(1) + uidBytes(10) + nameLen(1) + nameBytes(48) = 60
        reg_days = struct.unpack_from("<H", rec, 60)[0]
        valid_days = struct.unpack_from("<d", rec, 62)[0]
        assert reg_days == 0xFFFF
        assert valid_days == -1.0

    def test_crc32_valid(self):
        entry = {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        stored_crc = struct.unpack_from("<I", rec, convert.RECORD_SIZE - 4)[0]
        import zlib
        actual_crc = zlib.crc32(rec[:convert.RECORD_SIZE - 4]) & 0xFFFFFFFF
        assert stored_crc == actual_crc

    def test_empty_name_rejected(self):
        entry = {"uid": "04AABBCCDD", "name": "", "registered": "2025-01-15", "valid_days": 30}
        try:
            convert.encode_record(entry, "test", "2025-07-13")
            assert False, "Should have raised ConvertError"
        except convert.ConvertError:
            pass

    def test_negative_valid_days_rejected(self):
        entry = {"uid": "04AABBCCDD", "name": "Bob", "registered": "2025-01-15", "valid_days": -5}
        try:
            convert.encode_record(entry, "test", "2025-07-13")
            assert False, "Should have raised ConvertError"
        except convert.ConvertError:
            pass


class TestUtf8SafeNameTruncation:
    """Regression tests for the mid-character truncation bug: a name whose
    encode()[:MAX_NAME_LEN] byte slice landed inside a multi-byte UTF-8
    character used to leave a dangling lead byte, which decoded back as
    U+FFFD on readback instead of the original character."""

    def test_name_exactly_at_limit_is_unchanged(self):
        # 48 plain ASCII bytes -- right at MAX_NAME_LEN, should pass through untouched.
        name = "A" * 48
        entry = {"uid": "04AABBCCDD", "name": name, "valid_days": 30}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        decoded = convert.decode_record(rec, "test")
        assert decoded["name"] == name

    def test_multibyte_char_at_boundary_truncates_whole_character(self):
        # 47 ASCII bytes + one 2-byte char = 49 bytes, one over the limit.
        # A safe truncation must drop the whole trailing char, not half of it.
        name = "A" * 47 + "\u00e9"  # "é"
        entry = {"uid": "04AABBCCDD", "name": name, "valid_days": 30}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        decoded = convert.decode_record(rec, "test")
        assert decoded["name"] == "A" * 47
        assert "\ufffd" not in decoded["name"]  # no replacement-character corruption

    def test_helper_never_splits_a_character(self):
        # Direct check on the helper across a range of multi-byte boundaries.
        for pad in range(44, 50):
            name = "A" * pad + "\u00e9"
            encoded = convert.utf8_safe_truncate(name, convert.MAX_NAME_LEN)
            assert len(encoded) <= convert.MAX_NAME_LEN
            # Must always be valid, decodable UTF-8 -- a split character
            # would raise UnicodeDecodeError here.
            encoded.decode("utf-8")


class TestDecodeRecord:
    def test_roundtrip(self):
        entry = {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        decoded = convert.decode_record(rec, "test")
        assert decoded["uid"] == "04AABBCCDD"
        assert decoded["name"] == "Alice"
        assert decoded["registered"] == "2025-01-15"
        assert decoded["valid_days"] == 30.0

    def test_admin_roundtrip(self):
        entry = {"uid": "04AABBCCDD", "name": "Admin"}
        rec = convert.encode_record(entry, "test", "2025-07-13")
        decoded = convert.decode_record(rec, "test")
        assert decoded["uid"] == "04AABBCCDD"
        assert decoded["name"] == "Admin"
        assert decoded["registered"] is None
        assert decoded["valid_days"] is None

    def test_corrupt_crc_rejected(self):
        entry = {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30}
        rec = bytearray(convert.encode_record(entry, "test", "2025-07-13"))
        rec[12] ^= 0xFF  # flip a byte in the name
        try:
            convert.decode_record(bytes(rec), "test")
            assert False, "Should have raised ConvertError"
        except convert.ConvertError as e:
            assert "CRC32" in str(e)

    def test_truncated_record_rejected(self):
        try:
            convert.decode_record(b"\x00" * 10, "test")
            assert False, "Should have raised ConvertError"
        except convert.ConvertError as e:
            assert "truncated" in str(e)


class TestJsonToBin:
    def test_roundtrip_file(self):
        users = [
            {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
            {"uid": "5AF73581", "name": "Bob"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json
            json.dump(users, f)
            json_path = f.name

        bin_path = json_path.replace(".json", ".bin")
        try:
            convert.json_to_bin(json_path, bin_path)
            assert os.path.exists(bin_path)

            with open(bin_path, "rb") as f:
                data = f.read()
            assert data[:4] == b"RUD1"
            n = (len(data) - convert.HEADER_SIZE) // convert.RECORD_SIZE
            assert n == 2
        finally:
            os.unlink(json_path)
            if os.path.exists(bin_path):
                os.unlink(bin_path)


class TestBinToJson:
    def test_roundtrip_file(self):
        users = [
            {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
            {"uid": "5AF73581", "name": "Bob"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json
            json.dump(users, f)
            json_path = f.name

        bin_path = json_path.replace(".json", ".bin")
        out_path = json_path.replace(".json", "_out.json")
        try:
            convert.json_to_bin(json_path, bin_path)
            convert.bin_to_json(bin_path, out_path)

            with open(out_path) as f:
                result = json.load(f)
            assert len(result) == 2
            assert result[0]["uid"] == "04AABBCCDD"
            assert result[0]["name"] == "Alice"
            assert result[1]["registered"] is None  # admin
            assert result[1]["valid_days"] is None
        finally:
            for p in (json_path, bin_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)


class TestComputeCrc32:
    def test_deterministic(self):
        entries = [("04AABBCCDD", "Alice", "2025-01-15", 30), ("5AF73581", "Bob", None, None)]
        crc1 = convert.compute_canonical_crc32(entries, "2025-07-13")
        crc2 = convert.compute_canonical_crc32(entries, "2025-07-13")
        assert crc1 == crc2

    def test_different_order_same_crc(self):
        entries_a = [("04AABBCCDD", "Alice", "2025-01-15", 30), ("5AF73581", "Bob", None, None)]
        entries_b = [("5AF73581", "Bob", None, None), ("04AABBCCDD", "Alice", "2025-01-15", 30)]
        assert convert.compute_canonical_crc32(entries_a, "2025-07-13") == \
               convert.compute_canonical_crc32(entries_b, "2025-07-13")

    def test_different_entries_produce_valid_crc(self):
        entries = [("04AABBCCDD", "Alice", "2025-01-15", 30), ("5AF73581", "Bob", None, None)]
        crc = convert.compute_canonical_crc32(entries, "2025-07-13")
        assert isinstance(crc, int)
        assert 0 <= crc <= 0xFFFFFFFF


class TestSyncWireHelpers:
    def test_manifest_entry_size(self):
        assert convert.MANIFEST_ENTRY_SIZE == 1 + convert.UID_BYTES + 4

    def test_uid_entry_size(self):
        assert convert.UID_ENTRY_SIZE == 1 + convert.UID_BYTES

    def test_encode_uid_entry_length(self):
        entry = convert.encode_uid_entry("04AABBCCDD")
        assert len(entry) == convert.UID_ENTRY_SIZE

    def test_encode_uid_entry_roundtrips_uid_len(self):
        entry = convert.encode_uid_entry("04AABBCCDD")
        assert entry[0] == 5  # 5 bytes = 10 hex chars

    def test_encode_uid_entry_rejects_bad_uid(self):
        with pytest.raises(convert.ConvertError):
            convert.encode_uid_entry("ZZ")

    def test_decode_manifest_entry_roundtrip(self):
        today = "2025-07-13"
        rec = convert.encode_record(
            {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
            "uid 04AABBCCDD", today,
        )
        # Manifest entry = uid prefix + record's own trailing CRC32.
        manifest_entry = rec[:1 + convert.UID_BYTES] + rec[-4:]
        uid, crc = convert.decode_manifest_entry(manifest_entry, "entry 0")
        assert uid == "04AABBCCDD"
        assert crc == struct.unpack_from("<I", rec, len(rec) - 4)[0]

    def test_decode_manifest_entry_truncated_raises(self):
        with pytest.raises(convert.ConvertError):
            convert.decode_manifest_entry(b"\x00" * 3, "entry 0")

    def test_manifest_entries_differ_when_record_differs(self):
        today = "2025-07-13"
        rec_a = convert.encode_record(
            {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 30},
            "uid 04AABBCCDD", today,
        )
        rec_b = convert.encode_record(
            {"uid": "04AABBCCDD", "name": "Alice", "registered": "2025-01-15", "valid_days": 31},
            "uid 04AABBCCDD", today,
        )
        _, crc_a = convert.decode_manifest_entry(rec_a[:1 + convert.UID_BYTES] + rec_a[-4:], "a")
        _, crc_b = convert.decode_manifest_entry(rec_b[:1 + convert.UID_BYTES] + rec_b[-4:], "b")
        assert crc_a != crc_b

