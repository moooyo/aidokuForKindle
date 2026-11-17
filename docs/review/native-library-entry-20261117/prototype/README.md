# Unadopted entry experiments

These are historical research sources, not production components or installable
entry points. The runtime package contains none of them.

- `bridge.c` and `native_entry_test.py` originally lived at
  `src/native_entry/bridge.c` and `packaging/tests/native_entry_test.py`.
  Eleven isolated host tests passed. The native application-manager handshake
  worked, but application-level filename associations did not replace the
  shared MIME handler in the device's default file routing. Waiting for the
  native unload acknowledgement also introduced an unwanted delay.
- `dispatch-hooks/` contains the qualified-wrapper experiment. It retained the
  installed shared executable and its scanner/privilege behavior while using
  opt-in hooks to wake the main wait early and skip one xrefresh. Fourteen host
  tests passed, including original-environment restoration and hash mismatch
  fallback. Real default file routing exercised it successfully, but the
  six-round fixed-registration comparison did not establish a performance
  improvement. It is not retained.

The scripts preserve their original source-relative paths for provenance.
Original build directories and full evidence remain on test-env under
`/root/aidoku-native-entry-20261117`. Temporary registrations, handler command
changes, and document replacements were restored after each owned batch.
