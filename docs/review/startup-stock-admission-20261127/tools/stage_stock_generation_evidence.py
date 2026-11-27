#!/usr/bin/env python3
"""Analyze one closed f3e5070 stock diagnostic without device access."""

import argparse
import json
from pathlib import Path
import re
import runpy


HERE = Path(__file__).resolve().parent
RUNNER = runpy.run_path(str(HERE/'run_stock_generation.py'))
private_path, SOURCE, DIAGNOSTIC, OPERATOR = (RUNNER[key] for key in ('private_path', 'SOURCE', 'DIAGNOSTIC', 'OPERATOR'))


def analyze(attempt):
    if attempt.parent != HERE or re.fullmatch(r'attempt-[a-f0-9]{16}', attempt.name) is None:
        raise ValueError('Expected a closed attempt in this workspace')
    receipt = json.loads((attempt/'result.json').read_bytes())
    release = json.loads((attempt/'released.json').read_bytes())
    if not (receipt['source'] == SOURCE and receipt['diagnostic'] == DIAGNOSTIC and receipt['operator'] == OPERATOR
            and receipt['mode'] == 'measure' and receipt['complete'] is True
            and receipt['published_program_identical'] is True and receipt['run_status'] == 0
            and receipt['closeout'] == 'restored' and receipt['recovery_required'] is False and receipt['release'] == release
            and release['state'] == 'released' and all(release[k] is True for k in (
                'owner_and_token_checked','native_restored_and_connections_closed','lease_directory_absent_after_release'))):
        raise ValueError('Measurement was not positively restored and released')
    # Reuse the exact observer/pixel checks and initial-loading classification.
    # The old analyzer needs an old-format receipt, so keep its evidence reader
    # and predicates but supply the new, separately release-checked boundary.
    stages = runpy.run_path(str(private_path('stock_templates')/'stage_evidence.py'))
    prior = runpy.run_path(str(stages['PREVIOUS']/'analyze-first-screen.py'))
    observer = runpy.run_path(str(prior['OBSERVER']))
    row, = json.loads((attempt/'clock-records.json').read_bytes())
    shape, _ = observer['frame'](attempt/'control-01.frame')
    samples, observation = observer['trace'](attempt/'control-01.trace', shape)
    if not samples[1][1] < row['release_before_us'] <= row['release_after_us'] < row['library_send_us'] < samples[-1][0]:
        raise ValueError('Stimulus or Library outside the sampling envelope')
    loading = prior['loading_marks'](samples, row['release_before_us'], row['release_after_us'], row['library_send_us'],
                                     prior['SOURCE']/'assets/startup', observer['XS'], observer['YS'])
    if loading is None:
        raise ValueError('No unique loading-frame match in the private trace')
    return {'source': SOURCE, 'operator': OPERATOR, 'scope': 'One normal-card visit with the temporary launcher-only trace',
            'release': release,
            'no_optical_result': True, 'observer': observation, 'shelf': row['library'],
            **stages['attribute'](row, loading), 'launcher_breakdown': row['launcher_breakdown'],
            'generation_breakdown': row['generation_breakdown']}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('attempt', type=Path)
    args = ap.parse_args()
    print(json.dumps(analyze(args.attempt.resolve(strict=True)), indent=2))
