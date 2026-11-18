#!/usr/bin/env python3
"""Derive independent cold-only tools from a pinned, unmodified warm harness."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys


SHA = {
    'run_bench.py': '85d60c15b871375477cd14ef2020f0cc6adb7119ef618a010bec6ed934d97f7c',
    'bench_device.sh': '3126a4c5ceffb3836218f9f9ccb9dab2b111c67c56a0d380c98a8167379da14d',
}
OLD = Path('/root/aidoku-next-bench-20261118/tools')
DEST = Path('/root/aidoku-next-coldbench-20261118')


def replace(source, old, updated):
    if source.count(old) != 1:
        raise ValueError('Pinned integration anchor changed: ' + old[:65])
    return source.replace(old, updated, 1)


def generate(src=OLD):
    base = {}
    for name, expected in SHA.items():
        raw = (src/name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Warm base identity differs: ' + name)
        base[name] = raw.decode()
    runner = base['run_bench.py']
    runner = replace(runner, str(OLD.parent), str(DEST))
    runner = replace(runner, '    variants = {}; files = {}; flags = {}', '''    if (ns.primary != 'covers' or ns.baseline_app or not ns.candidate_app or
            any(getattr(ns, side + '_' + field) for side in TOKENS for field in ('launcher', 'arg', 'env', 'extra'))):
        raise ValueError('Cold comparison requires the installed baseline, one candidate ELF and no switches')
    if sha(ns.candidate_app.read_bytes()) == APP_SHA:
        raise ValueError('The candidate is the baseline')
    variants = {}; files = {}; flags = {}''')
    runner = replace(runner, 'AIDOKU_RESEARCH_DATA="$stage/data"', 'AIDOKU_RESEARCH_DATA="$stage/cases/$case_id/data"')
    runner = replace(runner, 'AIDOKU_RESEARCH_DWELL=8000;', 'AIDOKU_RESEARCH_DWELL=20000;')
    runner = replace(runner, 'AIDOKU_RESEARCH_DWELL=12500;', 'AIDOKU_RESEARCH_DWELL=24000;')
    runner = replace(runner, "'cases':cases,'variants':variants", "'cache_regime':'empty-cache-v1','fixture_policy':'per-case-empty-cache-v1','cases':cases,'variants':variants")
    dev = base['bench_device.sh']
    left = dev.index('fixture() {\n'); right = dev.index('prepare_device() {\n',left)
    dev = dev[:left] + Path(__file__).with_name('cold_device.sh').read_text() + '\n' + dev[right:]
    dev = replace(dev, '    savedlog=$STAGE/data/logs/launcher.log;', '    prepare_case_cold "$C"\n    savedlog=$STAGE/cases/$C/data/logs/launcher.log;')
    dev = replace(dev, '    stable_entry; printf \'ok\\n\' >"$OUT/case-ok"', '    check_cold_case\n    stable_entry; printf \'ok\\n\' >"$OUT/case-ok"')
    dev = replace(dev, '    cat /proc/sys/kernel/random/boot_id', '    real_cache_snapshot || return 1\n    cat /proc/sys/kernel/random/boot_id')
    dev = replace(dev, 'is_app "$p" "$variant"; sleep 6', 'is_app "$p" "$variant"; sleep 16')
    dev = replace(dev, 'is_app "$p" "$variant"; "$STAGE/capture" >"$OUT/native-settled.ppm"', 'is_app "$p" "$variant"; mark native_capture_started; "$STAGE/capture" >"$OUT/native-settled.ppm"')
    return {'run_bench.py':runner,'bench_device.sh':dev}


def main():
    if sys.platform != 'linux': raise SystemExit('Run only on test-env')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=OLD);args=p.parse_args()
    tools = DEST/'tools'; tools.mkdir(parents=True,exist_ok=True)
    for name, text in generate(args.source).items():
        dst=tools/name
        if dst.exists() and dst.read_text() != text:
            raise ValueError('Existing cold tool differs; use a new qualified tooling directory/version')
        if not dst.exists():dst.write_text(text)
        print(name,hashlib.sha256(text.encode()).hexdigest())
    subprocess.run(['sh','-n',tools/'bench_device.sh'],check=True)
    subprocess.run([sys.executable,'-I','-m','py_compile',tools/'run_bench.py'],check=True)


if __name__=='__main__':main()
