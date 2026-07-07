# Changelog

## v1.0.0

### Bug fixes

- **JSON escaping:** a user name containing `"` or `\` corrupted `users.json`
  on the next save, which then failed to parse on the next boot and silently
  wiped the entire database. Names are now properly JSON-escaped everywhere
  they're written (`DatabaseManager::save()`, `SerialProtocol::sendUserList()`).
  Control characters (newline/tab/etc.) are now rejected at validation time
  since they'd still garble the LCD even when escaped correctly.

- **O(log n) lookups:** every badge scan, `remove`, `rename`, and `tag-renew`
  did a linear O(n) scan over the user list despite an index existing --
  `uidIndex_` is now a real uid-to-position map, so those operations are
  O(log n).

- **Idempotency-aware retries:** the Python CLI could report a false failure
  ("Duplicate UID" / "UID not found") for an `add`/`remove`/`rename` that
  actually succeeded, if the device's reply arrived after the 2s timeout and
  got retried. Retries are now aware these commands aren't idempotent.

### New features

- **Automatic backup before wipe:** `remove --force` and `import --clear`
  now save an automatic timestamped backup of the current device database to
  `python_cli/backups/` before wiping anything (best-effort -- a backup
  failure is logged but does not block the operation you asked for).

- **Anti-brute-force lockout:** after `MAX_CONSECUTIVE_DENIALS` (default 5)
  consecutive denied badges, the reader stops accepting cards for
  `LOCKOUT_DURATION_MS` (default 30s). Both tunable in `Config.h`. The
  serial/CLI link stays fully usable during a lockout.

- **Runtime timezone:** timezone is now configurable at runtime via
  `python cli.py timezone --offset SECONDS [--dst SECONDS]` -- persisted on
  the device (NVS), no reflash required. `Config.h`'s
  `NTP_GMT_OFFSET_SEC`/`NTP_DAYLIGHT_OFFSET_SEC` are now only the
  first-boot default.
