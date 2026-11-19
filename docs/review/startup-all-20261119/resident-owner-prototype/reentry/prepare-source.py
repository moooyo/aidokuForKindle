#!/usr/bin/env python3
"""Assemble a new, source-bound study directory without changing the product checkout."""
import argparse
import hashlib
from pathlib import Path
import shutil


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Unexpected installed source shape: {old[:80]}')
    return text.replace(old, new)


def prepare(source, destination):
    root, out = source.resolve(), destination.resolve()
    if root == out or root in out.parents or out.exists():
        raise ValueError('The destination must be a new directory outside the source checkout')
    here = Path(__file__).resolve().parent
    rs = out / 'src'
    (rs / 'native_presentation/portrait_writer').mkdir(parents=True)
    files = {
        'src/native_presentation.rs': '91ac1748bc501b68493c37688f663aac1a941635a1ea0d9a950cb4fa8b98f4f9',
        'src/native_presentation/portrait_writer.rs': '6d54440c56243eb56c5f951bd90fbb2dc11fc03e7fa31271819cc3b7bafd1f47',
        'src/native_query.rs': '7c1ca54f9f8c61f5d6d76ac97fd672a801559574e7b62024d43fc5d18d109442',
    }
    for name, digest in files.items():
        content = (root / name).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f'Installed source fingerprint changed: {name}')
        (out / name).write_bytes(content)
    writer = rs / 'native_presentation/portrait_writer.rs'
    s = writer.read_text()
    start = s.index('    let deadline = Instant::now() + TRANSACTION_TIMEOUT;', s.index('pub(super) fn capture_startup('))
    end = s.index('    writer.publish(STARTUP_RECORD', start)
    stock = replace(s[start:end], 'writer.lease()? == lease', 'writer.lease()? == *lease')
    s = s[:start] + '    let (calibration, sleep_supported) = observe_stock(&writer, &lease, profile, observe_sleep, &cancelled)?;\n' + s[end:]
    between = s.index('fn stock_sleep_modules(')
    s = s[:between] + '''// Every Library presentation observes the same bounded stock state while native Home is active.
fn observe_stock(writer: &Writer, lease: &LeaseIdentity, profile: DeviceProfile, observe_sleep: bool,
                 cancelled: &impl Fn() -> bool) -> Result<(StartupCalibration, Option<bool>)> {
''' + stock + '    Ok((calibration, sleep_supported))\n}\n\npub mod library_api;\n\n' + s[between:]
    writer.write_text(s)
    pf = rs / 'native_presentation.rs'
    s = replace(pf.read_text(), 'mod portrait_writer;', 'mod portrait_writer;\npub use portrait_writer::library_api as library_return;')
    s = replace(s, '#[derive(Clone)]\npub struct NativePresentationContext', 'pub struct NativePresentationContext')
    s = replace(s, '    startup_raw: String,\n    application: u32,', '    startup_raw: String,\n    library_handoffs: bool,\n    application: u32,')
    s = replace(s, '            startup_raw,\n            application:', '            startup_raw,\n            library_handoffs: std::env::var_os("AIDOKU_LIBRARY_SESSIONS").as_deref() == Some(std::ffi::OsStr::new("1")),\n            application:')
    s = replace(s, '    fn ensure_startup_current(&self) -> Result<()> {', '''    fn ensure_startup_current(&self) -> Result<()> {
        if self.library_handoffs {
            ensure!(self.records.read_optional("library-closed-0")?.is_none(),
                "The original Library presentation was relinquished");
        }''')
    pf.write_text(s)
    shutil.copyfile(here / 'library-api.rs', rs / 'native_presentation/portrait_writer/library_api.rs')
    shutil.copyfile(here.parent / 'native-library-visit.rs', rs / 'native_library_visit.rs')
    shutil.copyfile(here / 'probe.rs', rs / 'main.rs')
    shutil.copyfile(here.parent / 'Cargo.toml', out / 'Cargo.toml')
    shutil.copyfile(root / 'Cargo.lock', out / 'Cargo.lock')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    prepare(args.source, args.destination)