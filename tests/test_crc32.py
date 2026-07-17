#!/usr/bin/env python3
"""Regression tests for the canonical database CRC32."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python_cli"))

import convert

TODAY = "2026-07-16"


def crc_for(entries):
    return convert.compute_canonical_crc32(entries, TODAY)


class TestCanonicalCrc32NotCountOnly:
    """Regression tests for the historical count-only CRC bug."""

    def test_same_count_different_content_gives_different_crc(self):
        one_user_a = [("AE4521B5", "René", None, None)]
        one_user_b = [("A245BC12", "A" * 48, None, None)]
        assert crc_for(one_user_a) != crc_for(one_user_b)

    def test_same_count_different_content_gives_different_crc_n2(self):
        two_users_a = [
            ("AE4521B5", "René", None, None),
            ("11111111", "Alice", None, None),
        ]
        two_users_b = [
            ("AE4521B5", "René", None, None),
            ("22222222", "Bob", None, None),
        ]
        assert crc_for(two_users_a) != crc_for(two_users_b)

    def test_crc_is_not_purely_a_function_of_record_count(self):
        """Different databases with the same record count must not share a CRC."""

        def make(n, seed):
            return [
                (f"{seed + i:08X}", f"User{seed + i}", None, None)
                for i in range(n)
            ]

        for n in (1, 2, 3, 5):
            crcs = {crc_for(make(n, seed)) for seed in (0, 1000, 5_000_000)}
            assert len(crcs) > 1


class TestCanonicalCrc32Consistency:
    """Basic correctness and determinism."""

    def test_identical_databases_give_identical_crc(self):
        db1 = [("AE4521B5", "René", None, None)]
        db2 = [("AE4521B5", "René", None, None)]
        assert crc_for(db1) == crc_for(db2)

    def test_identical_databases_with_multiple_users_give_identical_crc(self):
        db1 = [
            ("AE4521B5", "René", None, None),
            ("11111111", "Alice", "2024-01-01", 30),
        ]
        db2 = [
            ("AE4521B5", "René", None, None),
            ("11111111", "Alice", "2024-01-01", 30),
        ]
        assert crc_for(db1) == crc_for(db2)

    def test_input_order_does_not_affect_crc(self):
        """CRC must be independent of input order."""
        db_a = [
            ("11111111", "Alice", None, None),
            ("AE4521B5", "René", None, None),
        ]
        db_b = [
            ("AE4521B5", "René", None, None),
            ("11111111", "Alice", None, None),
        ]
        assert crc_for(db_a) == crc_for(db_b)

    def test_single_field_change_changes_crc(self):
        base = [("AE4521B5", "René", None, None)]
        renamed = [("AE4521B5", "Renee", None, None)]
        assert crc_for(base) != crc_for(renamed)

    def test_no_longer_equals_the_old_magic_residue_constant(self):
        """Regression check against the old magic-residue bug."""
        db = [("AE4521B5", "René", None, None)]
        assert crc_for(db) != 0x2144DF1C


class TestImportShortcutSemantics:
    """Import should be skipped only for identical databases."""

    def _would_skip_import(self, device_entries, file_entries):
        return crc_for(device_entries) == crc_for(file_entries)

    def test_shortcut_does_not_fire_for_different_content_same_count(self):
        device_state = [("AE4521B5", "René", None, None)]
        incoming_file = [("A245BC12", "A" * 48, None, None)]
        assert not self._would_skip_import(device_state, incoming_file)

    def test_shortcut_fires_only_for_truly_identical_databases(self):
        device_state = [
            ("AE4521B5", "René", None, None),
            ("11111111", "Alice", "2024-01-01", 30),
        ]
        identical_file = [
            ("11111111", "Alice", "2024-01-01", 30),
            ("AE4521B5", "René", None, None),
        ]
        assert self._would_skip_import(device_state, identical_file)

    def test_shortcut_does_not_fire_when_one_field_differs(self):
        device_state = [("AE4521B5", "René", None, None)]
        almost_same = [("AE4521B5", "Renae", None, None)]
        assert not self._would_skip_import(device_state, almost_same)

    def test_shortcut_does_not_fire_for_different_record_count(self):
        device_state = [("AE4521B5", "René", None, None)]
        bigger_file = [
            ("AE4521B5", "René", None, None),
            ("11111111", "Alice", None, None),
        ]
        assert not self._would_skip_import(device_state, bigger_file)