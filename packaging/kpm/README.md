# Aidoku KPM Package

This package uses the existing KPM installation and the Kindle home-screen
Scriptlet entry. It does not install a different launcher or replace a global
document hook. Build, package, and verify through `ssh test-env`.

## Package a prebuilt application

Build the production Kindle ARM executable separately with at most six Cargo
jobs, then package that exact output from the production source tree:

```sh
python3 scripts/package-kpm.py \
  --application target/armv7-unknown-linux-musleabihf/release/aidoku \
  --output dist/aidoku-0.1.1.kpkg
```

The numeric package version comes from `--version`, `CARGO_PKG_VERSION`, or
`Cargo.toml`, in that order. The builder never invokes Cargo. An optional
`--sample-cbz PATH` includes only an operator-selected public, redistributable
sample as `aidoku/samples/Aidoku-Sample.cbz`. Without this option, no example
comic is installed or imported.

The gzip-compressed tar archive contains a version-2 `manifest.json`, KPM
`install.sh`, `launch.sh`, `uninstall.sh`, an immutable `payload.tsv`, and the
allowlisted `payload/`. The payload retains the complete project license,
third-party notices, backend provenance, source-engine notices, font notices,
icon notices, demo provenance, and startup artwork provenance when present.
Fonts, dictionaries, icons, and startup pixels remain embedded in the ELF.
Runtime data, credentials, accounts, caches, downloaded comics, and automatic
exit options are excluded. The adjacent `.manifest.json` binds all package
members and the prebuilt executable by SHA-256.

## Installation and rollback

For a fresh installation with no Aidoku registration, install the archive with
the existing KPM installation using a local file URL:

```sh
/var/local/kmc/bin/kpm -y install file:///mnt/us/aidoku-0.1.1.kpkg
```

The installation hook updates only files named by `payload.tsv`; executable and launcher identities
come from their unique records rather than a source-level artifact hash.
Private program and metadata backups remain below
`/mnt/us/.aidoku-install-backups/`. Existing data, source configuration,
progress, and a valid device profile remain in place. Only a missing profile
receives the default `scribe` value. An optional public sample is copied into
imports only when that import does not already exist.

### Updating this project from 0.1.0 to 0.1.1 on KPM 0.2.2

Use a controlled Aidoku-only removal followed by a fresh installation. Official
KPM 0.2.2 compares an installed package id with the raw `file://` target before
reading that target's manifest. The existing Aidoku artifact can therefore stay
in the dependency graph alongside the new version and prevent resolution. See
the pinned [installation implementation](https://github.com/KindleModding/KPM/blob/07495e01d6fbb43afb8050116cef140fcbd086ea/src/install.c#L627-L717)
and [same-id conflict resolution](https://github.com/KindleModding/KPM/blob/07495e01d6fbb43afb8050116cef140fcbd086ea/src/dependencies.c#L644-L708).
A `gzip: unexpected end of file` warning alone does not establish archive or
transfer corruption; the complete gzip stream and tar contents must be checked
on the verification host before assigning that cause.

Before removing anything, close Aidoku and preserve verified copies of its old
program files, KPM package directory, Aidoku registration and dependency
records, and device profile. Keep the old verified package available for
recovery. Retain all user data and existing private backups. Independently
confirm that no other installed package depends on Aidoku; do not rely solely
on KPM 0.2.2's dependency check.

Remove only the Aidoku package through its supported normal uninstall:

```sh
/var/local/kmc/bin/kpm -y uninstall aidoku
```

Continue only after this succeeds, the Aidoku package directory and registration
are absent, and other installed packages are unchanged. Then install the new
local package:

```sh
/var/local/kmc/bin/kpm -y install file:///mnt/us/aidoku-0.1.1.kpkg
```

Aidoku's owned uninstall removes only unchanged program files named by its
completed allowlist. It preserves user data, the saved profile, changed files,
and private backups. The new hook also preserves an existing valid profile.
Stop on any command failure or incomplete state check. Recover only Aidoku from
the prepared old package and its scoped program, package, and registration
backups; never restore a whole shared KPM database or remove another package.

An installation hook's program rollback restores the state present when that
hook started. It does not restore a package removed by the preceding normal
uninstall, nor does it restore KPM's package directory or registration. The
completion marker is written only after all hook steps succeed, so KPM's
failed-install cleanup leaves program files restored by that hook alone.

## Home-screen launch and results

The installed `documents/Aidoku.sh` starts with these Scriptlet declarations:

```sh
#!/bin/sh
# Name: Aidoku
# DontUseFBInk
```

The legacy ZIP uses a separate direct document entry and does not require KPM
package registration.

The documented `DontUseFBInk` declaration suppresses Scriptlet stdout/stderr
rendering when it appears in the first six lines. See the
[Scriptlet documentation](https://kindlemodding.org/kindle-dev/scriptlets.html).
The entry redirects all three standard descriptors before calling
`/var/local/kmc/bin/kpm launch aidoku --asap`. The package launch hook redirects
its own descriptors and invokes the production launcher's default detached
mode. It does not pass `--foreground`, which remains available for supervised
host fixtures. No added delay is used as proof that a native UI has closed.

Private runtime logs are under `/mnt/us/aidoku/data/logs/`:

- `kpm-entry.log` receives the complete KPM call's output.
- `kpm-launch.log` records receipt by the package launch hook.
- `launcher.log` receives the detached worker and application output, including
  startup failures and the worker's final exit status after normal cleanup.

A successful KPM launch return acknowledges the detached request. It does not
claim that later initialization or application exit succeeded. The existing
launcher continues to own sleep/wake, ordinary exit, USB handoff, and restoration
of only the native resources that it changed. Hardware and firmware acceptance
remain separate from package creation.
