# Demo asset upgrade fixture

`demo-page-01-before.png` is the earlier application-owned demo page from the supplied design artwork. The upgrade E2E test places it only inside a fresh isolated demo data directory, then verifies that startup replaces it with the current bundled page without resetting reading progress, bookmarks, preferences, covers, or the seed marker.

This fixture is not included in the application binary and must never be copied over user-imported content.
