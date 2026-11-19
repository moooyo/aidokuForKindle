"""Measure one formerly offscreen cover in separate host application processes."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import tempfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_fixture(path):
    spec = importlib.util.spec_from_file_location('generated_import_books', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.bundle()


def run_arm(binary, mode, bundle, turn):
    contract = json.loads(bundle['fixture.json'])
    with tempfile.TemporaryDirectory(prefix='aidoku-native-import-') as name:
        root = Path(name)
        (root / 'synthetic-bench-only').touch()
        shelves = root / 'comics'
        shelves.mkdir()
        # Use the exact same archive bytes as the Scribe fixture. File names
        # make S05 the selected book; descending titles put S04 alone on page 2.
        originals = {}
        for rank, i in enumerate([*range(5, 13), 4]):
            path = shelves / f'{rank:02}.cbz'
            path.write_bytes(bundle[f'comics/S{i:02}.cbz'])
            originals[path] = contract['books'][i]['archive_sha256']
        data = root / 'data'
        data.mkdir()
        (data / 'startup-options').write_text(f'import_thumbnail={mode}\n')
        record = {'arm': mode}
        for phase in ('ingest', 'reopen'):
            env = dict(os.environ, AIDOKU_NATIVE_IMPORT_BENCH=str(root), AIDOKU_NATIVE_IMPORT_PHASE=phase,
                       AIDOKU_NATIVE_IMPORT_TURN=turn)
            proc = subprocess.run([binary, '--ignored', '--exact', 'app::import_thumbnails::bench::synthetic_phase',
                                   '--test-threads=1', '--nocapture'], env=env, text=True, capture_output=True, timeout=75)
            if proc.returncode:
                raise RuntimeError(f'{mode}/{phase}: {proc.stdout}\n{proc.stderr}')
            record.update(json.loads((root / (phase + '.json')).read_text()))
        if not (record['target_hidden'] and record['disk_entries'] == (9 if mode == 'idle' else 8)
                and record['disk_reused'] == (mode == 'idle')
                and record['target_source_sha256'] == contract['books'][4]['page_sha256'][0]
                and all(sha(path.read_bytes()) == digest for path, digest in originals.items())):
            raise RuntimeError(f'Cache state, visible set or immutable source mismatch: {record}')
        record['archives_unchanged'] = True
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('fixture_module', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--turn', choices=('quiet', 'immediate'), default='quiet')
    args = parser.parse_args()
    bundle = load_fixture(args.fixture_module)
    order = ['off', 'idle', 'idle', 'off', 'off', 'idle', 'idle', 'off']
    trials = []
    for mode in order:
        row = run_arm(args.binary.resolve(), mode, bundle, args.turn)
        trials.append(row)
        print(f"{len(trials)}/{len(order)}: {mode}, worker {row['page_turn_to_worker_ready_ms']:.3f} ms, "
              f"model {row['page_turn_to_model_ms']:.3f} ms", flush=True)
    if len({(tuple(r['dimensions']), r['rgb_sha256']) for r in trials}) != 1:
        raise RuntimeError('Foreground RGB pixels or dimensions differ across arms')
    fields = ['metadata_ms', 'visible_workers_ms', 'visible_model_ms', 'settled_ms', 'page_turn_to_worker_ready_ms', 'page_turn_to_model_ms']
    medians = {arm: {k: round(median(r[k] for r in trials if r['arm'] == arm), 3) for k in fields}
               for arm in ('off', 'idle')}
    pairs = [dict((t['arm'], t) for t in trials[i:i + 2]) for i in range(0, len(trials), 2)]
    deltas = {k: round(median(p['off'][k] - p['idle'][k] for p in pairs), 3) for k in fields}
    result = {'environment': 'ssh test-env; release x86_64 Linux test harness, headless Slint PW5, no device',
              'boundaries': 'Metadata and eight original visible workers are polled every 2 ms. Reopen is a new process; worker arrival and Slint model publication are separate and exclude physical display.',
              'turn': args.turn, 'test_binary_sha256': sha(args.binary.read_bytes()), 'fixture_manifest_sha256': sha(bundle['fixture.json']),
              'medians_ms': medians, 'median_paired_off_minus_idle_ms': deltas, 'trials': trials}
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
