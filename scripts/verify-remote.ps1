param(
    [string]$RemoteHost = 'test-env',
    [string]$RemoteDirectory = '/root/aidoku-build/source',
    [string]$RemoteTargetDirectory = '/root/aidoku-build/target',
    [string]$EmulatorRoot = '/root/aidoku-emulator/eink-emulator',
    [string]$GuestSsh = '/root/aidoku-emulator/guest-ssh',
    [string]$Machine = 'aidoku-pw6-5201',
    [switch]$SkipUi,
    [switch]$Kindle,
    [switch]$Firmware,
    [ValidateRange(1, 2147483647)]
    [int]$CargoBuildJobs = 12
)

$ErrorActionPreference = 'Stop'
$AidokuRepository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
foreach ($AidokuRemotePath in @($RemoteDirectory, $RemoteTargetDirectory, $EmulatorRoot, $GuestSsh)) {
    if ($AidokuRemotePath -notmatch '^/[A-Za-z0-9_./-]+$' -or $AidokuRemotePath.Contains('..')) {
        throw 'Remote paths must be simple absolute paths without parent traversal.'
    }
}
if ($RemoteHost -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$') {
    throw 'The worker must be an SSH alias or a simple hostname.'
}
if ($Machine -notmatch '^aidoku-[a-z0-9_-]+$') {
    throw 'Firmware checks require an isolated Aidoku emulator instance.'
}

& ssh -o BatchMode=yes -o ConnectTimeout=10 $RemoteHost "mkdir -p '$RemoteDirectory' '$RemoteTargetDirectory'"
if ($LASTEXITCODE -ne 0) { throw 'The designated remote verification environment is unavailable.' }

$AidokuSources = @('Cargo.toml', 'Cargo.lock', 'build.rs', '.cargo', 'src', 'ui', 'assets', 'vendor', 'examples', 'scripts', 'packaging', 'LICENSE', 'THIRD_PARTY_NOTICES.md')
$AidokuTransferPaths = foreach ($AidokuSource in $AidokuSources) {
    $AidokuPath = Join-Path $AidokuRepository $AidokuSource
    if (Test-Path -LiteralPath $AidokuPath) { $AidokuPath }
}
& scp -r @AidokuTransferPaths "${RemoteHost}:${RemoteDirectory}/"
if ($LASTEXITCODE -ne 0) { throw 'Source transfer failed.' }

$AidokuRemoteCommands = @(
    'set -eu',
    'if [ -f /root/.cargo/env ]; then . /root/.cargo/env; fi',
    "export CARGO_TARGET_DIR='$RemoteTargetDirectory' CARGO_BUILD_JOBS=$CargoBuildJobs",
    "cd '$RemoteDirectory'",
    'cargo fmt --all -- --check',
    'cargo test --locked --all-targets',
    'cargo test --locked --lib --features kindle platform::',
    'cargo test --locked --manifest-path vendor/slint-backend-kindle/Cargo.toml --lib',
    'python3 packaging/tests/launcher_test.py',
    'cargo build --locked',
    'sh -n packaging/kindle/launch.sh',
    'sh -n packaging/kual/set-profile.sh',
    'sh -n packaging/tests/emulator-wrapper.sh'
)
if (-not $SkipUi) {
    $AidokuRemoteCommands += 'python3 scripts/verify-ui.py --binary "$CARGO_TARGET_DIR/debug/aidoku" --output-dir artifacts/desktop'
}
if ($Kindle -or $Firmware) {
    $AidokuRemoteCommands += 'sh scripts/build-kindle.sh'
}
if ($Firmware) {
    $AidokuNativeCommand = 'python3 scripts/verify-firmware-emulator.py --binary "$CARGO_TARGET_DIR/armv7-unknown-linux-musleabihf/release/aidoku"'
    $AidokuRemoteCommands += $AidokuNativeCommand + " --emulator-root '$EmulatorRoot' --guest-ssh '$GuestSsh' --machine '$Machine' --output-dir artifacts/native"
}
& ssh -o BatchMode=yes -o ConnectTimeout=10 $RemoteHost ($AidokuRemoteCommands -join "`n")
if ($LASTEXITCODE -ne 0) { throw 'Remote verification failed; inspect the worker diagnostics and artifacts.' }

Write-Output "Remote verification completed on ${RemoteHost}. Artifacts: ${RemoteDirectory}/artifacts"
