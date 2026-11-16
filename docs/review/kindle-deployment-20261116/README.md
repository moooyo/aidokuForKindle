# Physical Scribe deployment

Date: 2026-11-16, Asia/Shanghai.

The user explicitly requested deployment of the completed 256-swatch candidate.
All 32 development-package files were installed and checked on the physical
Scribe, including `aidoku`, `kindle-jpeg-worker`, the launcher, document entry,
KUAL entries and notices. The archive SHA-256 is
`49b30748824d995f856e9c30de340aeb872cabcd79b67117078094ae9637929f`.

The installed production source has 335 files and fingerprint
`ffac5ba3b0166a1dae90d9a8240d4f9616915259da8a4223a939bd2261e23f80`.
See the [source manifest](../grayscale-256-20261115/source-manifest.json)
and [development-package verification](../grayscale-256-20261115/package-verification.json).

## Backup and data preservation

The program allowlist installer was reused with the ZIP's KUAL paths admitted.
It backed up replaced files and existing library/source metadata before writing
the program files. The private device backup is recorded in
[deployment.json](deployment.json); database contents, source preferences,
credentials and raw lease tokens are not included in this repository.

The installed library database and original source preferences matched their
backups byte for byte after verification. Downloaded content was outside the
program installation allowlist. Installation did not clear user data.

## Native verification

The initial installation succeeded and all 32 installed files matched, but the
first verification setup attempted symbolic links on Kindle user storage, which
does not support them. That staging was removed and its lease released. A new
verification batch copied the installed executables into an isolated test home,
checked their hashes, and used the established managed-launcher harness.
The program was not reinstalled for this retry.

Both native cases passed using offline demo data:

- Screen refresh opened the 256-swatch test. Its captured input bar contained
  every value from 0 through 255 in order, with matching RGB channels.
- Hardware settings reported JPEG acceleration available and off by default.
  The deployment checks submitted zero hardware decode jobs.

The physical device stayed in the same boot, with zero new critical kernel
messages. Native services, brightness and warmth were restored. Temporary test
files were removed, and both operation batches released their exact owned lease.
The retained backup remains on the device.

These checks establish native application startup, settings and input pixels.
They do not repeat codec benchmarks or establish optical grayscale levels.
The existing source-bound codec investigations retain their original scope.

## Evidence

- [Deployment and restoration summary](deployment.json).
- [Native settings and pixel checks](native-checks.json).
- [Grayscale page capture](native/grayscale.png) and [sanitized state](native/grayscale.state.json).
- [Hardware page capture](native/hardware.png) and [sanitized state](native/hardware.state.json).

Open Aidoku normally and select **Settings > Screen refresh** for the test strip,
or **Settings > Hardware** for the optional acceleration setting.
