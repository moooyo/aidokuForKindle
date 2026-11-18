#!/usr/bin/env python3
"""Summarize selected public analyzers without contacting a Kindle or reading private logs."""

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median


APPLICATION = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
COLD_APP = 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6'
LAUNCHER = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
RUN = re.compile(r'aidoku-next-[a-z][a-z0-9-]{0,20}-[0-9a-f]{8}')
SIDE = ('baseline', 'candidate')
ENDPOINTS = ('usable', 'covers', 'exit')
STATUSES = ('ok', 'missing', 'failed', 'deadline', 'censored')
GUARDS = ('success', 'lease_released', 'same_boot', 'entry_restored', 'native_restored',
          'installed_and_user_unchanged', 'input_roster_unchanged')
# Full protocol checks stay in the original analyzer. A separate export rule
# keeps private identities, arbitrary issue text and new unreviewed fields out.
SLOTS = {
    'supervisor-pilot': ('pilot', 'usable'),
    'supervisor-screen': ('screen', 'usable'),
    'prewarm-screen': ('screen', 'covers'),
    'cold-screen': ('screen', 'covers'),
    'cold-confirm': ('confirm', 'covers'),
    'cold-pixels': ('qualify', 'covers'),
    'cold-lifecycle': ('default-lifecycle', None),
    'last-frame-screen': ('screen', 'covers'),
    'cold-warm-guard': ('screen', 'covers'),
}
HELD = {
    'cold-lifecycle': {'status':'not_run_after_warm_guard', 'gate_passed':None,
        'reason':'The separate cold-only ELF failed its warm first-usable-frame protection threshold. No default lifecycle observations were collected.'},
    'cold-deployment': {'status':'not_run_after_warm_guard', 'gate_passed':None,
        'reason':'The cold-only ELF was not installed. Its cold speedup and separate pixel qualification do not override the failed warm check.'},
}
NEW_COMPOSITION = ('status-cold-screen','status-cold-confirm','status-warm-guard',
                   'status-pixels','status-lifecycle','status-deployment')
FAMILY = {'status-cold-screen':'cold-screen', 'status-cold-confirm':'cold-confirm',
          'status-warm-guard':'cold-warm-guard', 'status-pixels':'cold-pixels'}
SLOTS.update({k:SLOTS[v] for k,v in FAMILY.items()})
FULL_APP_TRIALS = {'cold-screen', 'cold-confirm', 'cold-pixels', 'cold-warm-guard', 'last-frame-screen'}
COLD_TRIALS = {'cold-screen', 'cold-confirm', 'cold-pixels'}
STOPPED_OBSERVATIONS = {
    'aidoku-next-native-pilot-689dcde0': dict(reason='device_unavailable', completed_pairs=0,
        observations='The read-only preflight could not reach the device. No device write or timed case occurred.',
        lease_released=True),
    'aidoku-next-cold-confirm-576fdfaf': dict(reason='guard_failed', completed_pairs=0,
        observations='The baseline warmup tripped the catalog recency/identity guard. The original entry bytes, cover, program, user library, lighting and native control were restored. The naturally generated catalog row was retained; no row was rewritten. A new complete batch is required.',
        lease_released=True),
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def normalized(value):
    return round(value, 3) + 0.0


def valid_time(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def evidence_identity(batch, slot, expected_status=None):
    clean = {}
    for side in SIDE:
        artifact = batch['artifacts'][side]
        row = {k: artifact[k] for k in ('application_sha256', 'launcher_sha256', 'native_query_sha256')}
        extras = artifact.get('extra_sha256', {})
        require(not(set(extras) - {'aidoku-startup-portrait'}), 'unexpected extra program')
        if extras:
            row['portrait_helper_sha256'] = extras['aidoku-startup-portrait']
        require(all(re.fullmatch('[a-f0-9]{64}', v) for v in row.values()), 'invalid program hash')
        require(row['native_query_sha256'] == QUERY, 'different native query helper')
        clean[side] = row
    a, b = [clean[side] for side in SIDE]
    base = FAMILY.get(slot,slot)
    if base in FULL_APP_TRIALS:
        require(a['application_sha256'] == APPLICATION != b['application_sha256'] and
                a['launcher_sha256'] == b['launcher_sha256'] == LAUNCHER and not any(
                    'portrait_helper_sha256' in s for s in (a, b)), 'not isolated application defaults')
        if slot in FAMILY:
            require(isinstance(expected_status,str) and re.fullmatch('[a-f0-9]{64}',expected_status) and
                    expected_status not in (COLD_APP,APPLICATION) and b['application_sha256']==expected_status,
                    'toolbar candidate does not match the independently frozen new ELF')
        elif slot in COLD_TRIALS or slot == 'cold-warm-guard':
            require(b['application_sha256'] == COLD_APP, 'unrecognized cold trial application')
    elif slot == 'prewarm-screen':
        require(a == b and a['application_sha256'] != APPLICATION, 'not a one-ELF prewarm switch')
    elif slot == 'supervisor-screen':
        require(a['application_sha256'] == b['application_sha256'] == APPLICATION and
                a['launcher_sha256'] == b['launcher_sha256'], 'not the same launcher and application')
    return clean


def intermediate(case):
    diag = case['diagnostics']
    frames = diag['library_frames_through_covers']
    require(type(frames) is int and frames >= 1, 'no complete Library frame')
    count = max(0, frames - 2)  # First and complete frames may coincide.
    if 'frame_grace' in case:
        require(case['frame_grace']['partial_frames'] == count, 'frame counts disagree')
    return count


def one_pair(pair, cases):
    n = pair['round']
    require(type(n) is int and 1 <= n <= 10 and pair['order'] in ('AB', 'BA'), 'unexpected timing round')
    ab = [cases.get(f'{side}-{n:02}') for side in SIDE]
    statuses = [c['status'] if c else 'missing' for c in ab]
    require(all(v in STATUSES for v in statuses), 'unexpected case status')
    complete = statuses == ['ok', 'ok']
    require(pair['complete'] is complete, 'case/pair completeness mismatch')
    out = {'round': n, 'order': pair['order'], 'case_status': statuses}
    if not complete:
        # An observed side remains inspectable even when its partner failed.
        # A partial pair has no saving and never enters a median or gate.
        out['unpaired_observations_ms'] = {
            side: {key: c['endpoints_ms'][key] for key in ENDPOINTS if valid_time(c['endpoints_ms'].get(key))}
            for side,c in zip(SIDE,ab) if c and isinstance(c.get('endpoints_ms'),dict)}
        return out
    for key in ENDPOINTS:
        a, b = [c['endpoints_ms'][key] for c in ab]
        require(valid_time(a) and valid_time(b) and a == pair['baseline_ms'][key] and b == pair['candidate_ms'][key],
                'case/pair duration mismatch')
        saving = normalized(a - b)
        require(abs(saving - pair['savings_ms'][key]) < 0.002, 'case/pair saving mismatch')
        out[key + '_ms'] = [a, b, saving]
    out['intermediate_frames'] = [intermediate(c) for c in ab]
    return out


def describe(pairs):
    full = [p for p in pairs if 'usable_ms' in p]
    result = {'planned_pairs': len(pairs), 'complete_pairs': len(full), 'endpoint_ms': {}}
    if not full:
        return result
    for key in ENDPOINTS:
        values = [p[key + '_ms'] for p in full]
        a, b, paired = map(median, zip(*values))
        result['endpoint_ms'][key] = dict(baseline_median=normalized(a), candidate_median=normalized(b),
            difference_of_medians=normalized(a-b), median_of_paired_savings=normalized(paired),
            candidate_faster_pairs=sum(s > 0 for _, _, s in values),
            by_order_median_paired_saving={order: normalized(median(subset)) for order in ('AB', 'BA')
                if (subset := [p[key + '_ms'][2] for p in full if p['order'] == order])})
    by_side = list(zip(*(p['intermediate_frames'] for p in full)))
    result['intermediate_frames'] = {'total': list(map(sum, by_side)),
        'runs_with_any': [sum(n > 0 for n in side) for side in by_side]}
    if all('first_render_ms' in p for p in full):
        result['first_render_ms'] = dict(zip(('baseline_median','candidate_median','median_of_paired_savings'),
            [normalized(median(group)) for group in zip(*(p['first_render_ms'] for p in full))]))
    return result


def mechanism_counts(slot, cases):
    grouped = {s: [r for r in cases if r['case'].startswith(s + '-') and r['status'] == 'ok'] for s in SIDE}
    if slot in COLD_TRIALS:
        return {s: {'combined_validation_jobs': sum(r['cold']['combined_successes'] for r in group),
                    'explicit_fallback_jobs': sum(r['cold']['explicit_fallbacks'] for r in group)} for s, group in grouped.items()}
    if slot == 'last-frame-screen':
        result = {}
        for side, group in grouped.items():
            result[side] = {k: sum(r['frame_grace'][k] for r in group) for k in ('started', 'completed_without_intermediate_frame')}
        return result
    if slot.startswith('supervisor-'):
        return {s: {k: sum(r['diagnostics']['portrait_engine'] == k for r in group) for k in ('native','shell','unmarked')} for s, group in grouped.items()}
    if slot == 'cold-warm-guard':
        return {s: {'warm_six_hits_zero_misses': sum(r['diagnostics']['reported_local_cache_hits'] == 6 and
                r['diagnostics']['reported_local_cache_misses'] == 0 for r in group)} for s, group in grouped.items()}
    return {s: {'attempted_runs': sum(r['diagnostics']['warm_attempts'] > 0 for r in group),
                'consumed_runs': sum(r['diagnostics']['cover_memory_hits'] > 0 for r in group)} for s, group in grouped.items()}


def toolbar_case(row):
    detail=row['toolbar']
    counts=('attempts','ready','unavailable','used','fallbacks','first_view_query_sessions',
            'first_view_query_responses','first_view_query_finishes')
    require(all(type(detail[k]) is int and detail[k]>=0 for k in counts), 'malformed toolbar counts')
    out={k:detail[k] for k in counts}
    for k in ('probe_wall_ms','first_view_ms','initial_send_to_input_ms'):
        if detail.get(k) is not None:
            require(valid_time(detail[k]), 'malformed toolbar duration'); out[k]=detail[k]
    if out['used']:
        out['sync_absent_on_hit']=detail.get('sync_absent_on_hit') is True
        require(out['sync_absent_on_hit'] and all(out[k]==0 for k in counts if k.startswith('first_view_query_')),
                'synchronous native activity reported during an adopted toolbar hit')
    return out


def pack_batch(b, slot, pixels, expected_status=None):
    run = b['run_id']; phase, primary = SLOTS[slot]
    require(RUN.fullmatch(run) and b['mode'] == phase, 'invalid batch identity or phase')
    cases = {r['case']: r for r in b['cases']}
    require(len(cases) == len(b['cases']) and all(re.fullmatch(r'(baseline|candidate)-(warmup|qualify|[0-9]{2})', n) for n in cases),
            'unexpected or repeated observation')
    pairs = [one_pair(p, cases) for p in b['pairs']]
    base = FAMILY.get(slot,slot)
    if base in COLD_TRIALS or base == 'cold-warm-guard':
        for pair in pairs:
            if 'usable_ms' not in pair:
                continue
            vals = [cases[side+'-%02d'%pair['round']]['diagnostics']['first_frame_cost_ms'][0] for side in SIDE]
            require(all(valid_time(t) for t in vals), 'invalid first render duration')
            pair['first_render_ms'] = vals + [normalized(vals[0]-vals[1])]
    roles = [c for n, c in cases.items() if n.endswith('-qualify') or n[-2:].isdigit()]
    visible = [r for r in roles if r['status'] == 'ok']
    for r in b['cases']:
        require(r['status'] in STATUSES, 'unexpected case status')
    out = dict(run_id=run, trial=slot, phase=phase,
        primary_endpoint='both_usable_and_covers' if base=='cold-warm-guard' else primary,
        cache_regime='empty_local_thumbnail_cache' if base in COLD_TRIALS else 'warm_local_thumbnail_cache',
        program_hashes=evidence_identity(b, slot, expected_status), batch_guards={k:b['batch_guards'].get(k) is True for k in GUARDS},
        reported_issue_count=len(b['issues']), excluded_warmups={n:c['status'] for n,c in cases.items() if n.endswith('warmup')},
        mechanism_counts=mechanism_counts(base, visible), pairs=pairs, summary=describe(pairs))
    if slot in FAMILY:
        out['toolbar_observations'] = {r['case']: toolbar_case(r) for r in visible}
    if phase == 'qualify':
        out['pixel_qualification'] = {'non_timing_cases': {r['case']:r['status'] for r in roles},
            'matched_below_live_status_bar': pixels.get(run)}
    return out


def public_report(path, slot, expected_status=None):
    require(path.parent.name == 'reports', 'expected a public analyzer report in reports/')
    data = json.loads(path.read_text()); phase,primary=SLOTS[slot]
    if slot == 'cold-lifecycle':
        run = data['run_id']
        require(re.fullmatch('aidoku-next-life-[0-9a-f]{8}', run) and data['candidates'] == ['cold'] and
                data['artifact_sha256'] == {'aidoku/app/aidoku': COLD_APP}, 'unexpected production defaults')
        keys = ('pixels', 'input', 'exit', 'sleep_wake', 'notification', 'catalog_identity', 'restoration', 'production_defaults')
        verified = {k: data['checks'].get(k) is True for k in keys}
        passed = all(verified.values())
        return [dict(run_id=run,trial=slot,checks=verified,application_sha256=COLD_APP,
            scope='Uninstrumented isolated production defaults, short powerd sleep/wake and software input ownership; no latency or physical touch claim.')], dict(
            status='completed' if passed else 'invalid_or_incomplete', run_ids=[run], gate_passed=passed, checks=verified)
    base=FAMILY.get(slot,slot)
    metrics=('usable','covers') if base=='cold-warm-guard' else (primary,)
    require(data['phase'] == phase and data['primary_metric'] in metrics, 'unexpected report phase or endpoint')
    runs = [pack_batch(b, slot, {p['batch']:p['identical_below_status_bar'] for p in data['pixels']}, expected_status) for b in data['batches'] if b['mode']==phase]
    require(len({r['run_id'] for r in runs}) == len(runs), 'repeated batch')
    pairs = [p for r in runs for p in r['pairs']]
    numbers = describe(pairs)
    parsed = data['valid'] is True and not data['issues'] and not data['input_failures'] and bool(runs)
    if parsed:
        if base in COLD_TRIALS:
            require(data.get('cold_fixture_match') is True and data.get('cold_fixture_verified') is True,
                    'cold fixture is not independently verified')
        require(all(all(r['batch_guards'].values()) and r['reported_issue_count']==0 for r in runs), 'invalid production guard')
        for k, item in numbers['endpoint_ms'].items():
            require(abs(item['median_of_paired_savings'] - data['comparisons_ms'][k]['paired_savings_ms']['median']) <= 0.002 and
                    item['candidate_faster_pairs'] == data['comparisons_ms'][k]['improved_pairs'], 'independent summary differs')
    gate = None
    if phase in ('screen','confirm') and parsed:
        expected,wins=(6,5) if phase=='screen' else (10,9)
        require(numbers['complete_pairs']==expected and sorted(p['round'] for p in pairs)==list(range(1,expected+1)) and
                Counter(p['order'] for p in pairs) == {'AB':expected//2,'BA':expected//2}, 'unbalanced or incomplete sampling')
        if base=='cold-warm-guard':
            require(all(sum(r['mechanism_counts'][s]['warm_six_hits_zero_misses'] for r in runs)==6 for s in SIDE), 'regression fixture is not fully warm')
            gate = all(numbers['endpoint_ms'][k]['median_of_paired_savings'] >= -50 for k in ('usable','covers'))
        else:
            main,guard = numbers['endpoint_ms'][primary], numbers['endpoint_ms']['usable' if primary=='covers' else 'covers']
            gate = main['median_of_paired_savings'] >= 100 and main['candidate_faster_pairs']>=wins and guard['median_of_paired_savings']>=-50
            require(gate is data['latency_gate_passed'], 'independent decision differs')
    if phase=='qualify' and parsed:
        gate = data['qualification_passed'] is True
    result=dict(status='completed' if parsed else 'invalid_or_incomplete',
        run_ids=[r['run_id'] for r in runs], analyzer_valid=parsed,
        analyzer_issue_count=len(data['issues'])+len(data['input_failures']), gate_passed=gate,
        mechanism_verified=None if slot=='cold-warm-guard' else data['mechanism'].get('verified') is True,
        summary=numbers)
    if slot in FAMILY:
        require(data['mechanism'].get('name')=='cold_decode_with_fresh_toolbar_status', 'unrecognized composition evidence')
        result['toolbar_verified']=data['status_mechanism'].get('verified') is True
        if base in COLD_TRIALS:
            result['cold_decode_verified']=data['cold_mechanism'].get('verified') is True
        if base=='cold-warm-guard' and parsed:
            require(gate is data['warm_guard_passed'], 'independent toolbar warm guard differs')
    return runs,result


def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',action='append',default=[],metavar='TRIAL=PUBLIC_REPORT')
    parser.add_argument('--stopped',action='append',default=[],metavar='TRIAL=RUN_ID:REASON')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--status-app-sha256',help='New independently frozen toolbar composition ELF; never the old cold-only binary')
    parser.add_argument('--status-unavailable',choices=('host_build_failed','host_verification_failed','no_qualified_artifact'))
    args = parser.parse_args()
    if sys.platform != 'linux': parser.error('Run on test-env')
    slots = {key:{'status':'pending_public_report'} for key in SLOTS}
    slots.update(HELD)
    slots.update({key:{'status':'not_yet_reported_for_new_elf',
        'note':'Independent conditional composition. No cold-only ELF samples transfer to this stage.'} for key in NEW_COMPOSITION})
    outcomes = {}
    for entry in args.report:
        key, sep, value = entry.partition('=')
        require(bool(sep) and key in SLOTS and slots[key]['status'] in ('pending_public_report','not_yet_reported_for_new_elf'),
                'unexpected, previously stopped, or repeated trial')
        runs, slots[key] = public_report(Path(value), key, args.status_app_sha256)
        for r in runs:
            require(r['run_id'] not in outcomes, 'duplicate run identity'); outcomes[r['run_id']]=r
    if args.status_unavailable:
        require(all(slots[key]['status']=='not_yet_reported_for_new_elf' for key in NEW_COMPOSITION), 'physical samples already reported')
        for key in NEW_COMPOSITION:
            slots[key]={'status':'not_run_host_candidate_unavailable','reason':args.status_unavailable,'gate_passed':None}
    guard=slots['status-warm-guard']
    if guard.get('status')=='completed' and (not guard.get('gate_passed') or not guard.get('toolbar_verified')):
        for key in NEW_COMPOSITION:
            if slots[key]['status']=='not_yet_reported_for_new_elf':
                slots[key]={'status':'not_run_after_status_warm_guard', 'gate_passed':None,
                    'reason':'The new composition failed the required warm protection or status-mechanism check.'}
    stopped=[]
    for item in args.stopped:
        key, sep,value=item.partition('=');name,_,reason=value.partition(':')
        require(sep and key in slots and RUN.fullmatch(name) and name not in outcomes and reason in (
            'preflight_rejected','harness_failed','aborted_before_sample','observation_incomplete','device_unavailable','guard_failed'), 'invalid stopped attempt')
        details = STOPPED_OBSERVATIONS.get(name, {'reason':reason})
        require(reason == details['reason'], 'stopped reason differs from the operator record')
        stopped.append(dict(trial=key,run_id=name,reason=reason)); outcomes[name]=dict(run_id=name,trial=key,
            status='stopped', provenance='Device operator recovery record; these observations are separate from validator-certified timing pairs.',
            used_in_decision=False, **details)
    index = dict(schema_version=1, units='milliseconds', method='Positive savings favor the candidate. Timing triples are [baseline, candidate, baseline minus candidate]. Intermediate frame tuples are [baseline, candidate], excluding first and complete frames. Usable is the later of first Library SEND and software input ready; exit is shutdown to native Home active. Screens, independent confirmations, and pixel qualifications are separate.',
        warm_guard='Six balanced pairs, each timed launch six derivative hits and zero misses: median paired savings >= -50 ms at both usable and covers. A regression check only; no significance, statistical noninferiority, or P95 claim.',
        slots=slots, stopped_attempts=stopped)
    args.out.mkdir(parents=True,exist_ok=True)
    for ident,row in outcomes.items(): write(args.out/(ident+'.json'),row)
    write(args.out/'index.json',index)
    print(json.dumps({key:value['status'] for key,value in slots.items()}))


if __name__ == '__main__':
    main()
