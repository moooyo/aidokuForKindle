#!/usr/bin/env python3
"""Export an explicit public allowlist from a completed R3 reopen audit."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ANALYZER = Path('/root/aidoku-all-bench-20261119/tools/analyze_r3_specialists.py')
PROGRAMS = {
    'application_sha256': '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af',
    'launcher_sha256': '412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675',
    'native_query_sha256': '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d',
    'portrait_helper_sha256': 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50',
}
OPTIONS = {
    'portrait': ('shell', 'native'), 'prewarm': ('0', '2'),
    'last_cover_grace': ('off', 'on'), 'import_thumbnail': ('off', 'idle'),
    'first_layout': ('baseline', 'optimized'), 'resident': ('off', '45'),
}
METRICS = tuple(
    f'{visit}_{metric}' for visit in ('prime', 'second')
    for metric in ('first', 'usable', 'covers', 'exit', 'post_covers_dwell', 'request_to_home')
) + (
    'home_gap', 'both_active_until_covers', 'both_exit_cost',
    'first_request_to_second_covers', 'first_request_to_second_home',
    'total_resource_observation', 'observations_during_home_gap',
)
MECHANISMS = {
    'ordinary_exit', 'retained_pixels_used', 'accepted_without_pixel_hit',
    'normal_reopen_fallback', 'expired_then_normally_reopened',
    'qualified_lost_keeper_fallback', 'qualification_warmup',
}


def need(value, message):
    if not value:
        raise ValueError(message)


def numeric(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [numeric(v) for v in value]
    if isinstance(value, dict):
        need(all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', k) for k in value), 'Unexpected statistic key')
        return {k: numeric(v) for k, v in value.items()}
    raise ValueError('A statistic contains nonnumeric data')


def actions(source):
    result = {}
    for visit in ('prime', 'second'):
        value = source[visit]
        selected = value['selection']
        need(set(selected) == set(OPTIONS), 'Incomplete observed options')
        need(all(selected[k] in OPTIONS[k] for k in OPTIONS), 'Unexpected observed option')
        result[visit] = {'selection': dict(selected)}
        for key in ('published', 'unavailable', 'reclaimed', 'rejected', 'pixel_hits', 'explicit_exit', 'publish_after_shutdown_start_ms'):
            result[visit][key] = numeric(value[key])
    return result


def resources(value):
    points = {}
    for key in ('between', 'pre_second', 'after_second'):
        item = value['points'][key]
        need(item['state'] in ('absent', 'held', 'gone'), 'Unexpected keeper state')
        points[key] = {'state': item['state']}
        points[key].update({k: numeric(item[k]) for k in ('rss_kib', 'pss_kib', 'sampling_window_ms')})
    return {'points': points, **{k: numeric(value[k]) for k in (
        'same_keeper_in_home', 'home_cpu_ticks', 'home_cpu_window_ms',
        'total_observation_ms', 'home_gap_observation_ms',
    )}}


def export(report, plan_sha):
    need(report.get('valid') is True, 'Do not export an invalid audit as accepted evidence')
    need(report['frozen_plan_sha256'] == plan_sha, 'The audit names another frozen plan')
    replay_sha = hashlib.sha256(ANALYZER.read_bytes()).hexdigest()
    need(report['replay_sha256'] == replay_sha, 'The replay tool changed')
    need(report['scope_name'] in ('immediate', 'ordinary', 'ttl', 'wake', 'screen'), 'Unsupported public scope')
    for side in ('baseline', 'candidate'):
        observed = report['artifacts'][side]
        need(all(observed[k] == PROGRAMS[k] for k in ('application_sha256', 'launcher_sha256', 'native_query_sha256')), 'Different R3 programs')
        need(observed['extra_sha256'] == {'aidoku-startup-portrait': PROGRAMS['portrait_helper_sha256']}, 'Different portrait helper')
        need(not observed['args'] and not observed['env'], 'Unexpected runtime overrides')

    batches, cases = [], []
    for batch in report['batches']:
        need(batch['scenario'] in ('immediate', 'ordinary', 'ttl'), 'Unexpected reopen scenario')
        batches.append({
            'scenario': batch['scenario'], 'mode': batch['mode'],
            'batch_guards': numeric(batch['batch_guards']),
            'protected_snapshots_match': batch['snapshots_match'],
            'release_verified': batch['release_verified'],
        })
        for row in batch['cases']:
            need(re.fullmatch(r'(baseline|candidate)-(warmup|qualify|[0-9]{2})', row['case']), 'Unexpected case label')
            need(row['status'] == 'ok' and not row['issues'], 'A planned episode did not pass')
            need(row['mechanism'] in MECHANISMS, 'Unexpected mechanism label')
            public = {k: row[k] for k in ('case', 'status', 'mechanism', 'options_verified', 'new_gui_process_for_each_visit', 'native_home_after_both_visits')}
            public['actions'] = actions(row['actions'])
            public['resources'] = resources(row['resources'])
            if 'ttl_wait_ms' in row:
                public['ttl_wait_ms'] = numeric(row['ttl_wait_ms'])
            if report['phase'] == 'screen' and row['case'].split('-')[1].isdigit():
                public['elapsed_ms'] = {k: numeric(row['elapsed_ms'][k]) for k in METRICS}
            cases.append(public)

    output = {
        'schema_version': 1, 'revision': 'R3', 'scope_name': report['scope_name'],
        'phase': report['phase'], 'verification_environment': 'test-env',
        'valid': report['valid'], 'qualified': report['qualified'],
        'qualification_passed': report['qualification_passed'],
        'latency_gate_passed': report['latency_gate_passed'],
        'programs': PROGRAMS, 'frozen_plan_sha256': plan_sha,
        'replay_sha256': replay_sha, 'batches': batches, 'cases': cases,
        'qualification_by_scenario': numeric(report['qualification_by_scenario']),
        'residency_resources': numeric(report['residency_resources']),
        'scope': 'Independent host replay of completed device observations. Each open uses a fresh GUI; only validated cover pixels can survive native Home. Qualification cases are not latency samples. RSS and PSS are observed process memory, not the decoded payload bound, peak memory, or battery use. Private identities, absolute clocks, pixels and image digests are excluded.',
    }
    if report['phase'] == 'screen':
        output['gate'] = {
            'minimum_primary_paired_median_ms': report['gate']['minimum_primary_paired_median_ms'],
            'minimum_faster_pairs': report['gate']['minimum_faster_pairs'],
            'maximum_secondary_paired_median_regression_ms': report['gate']['maximum_secondary_paired_median_regression_ms'],
            'primary_metric': 'second_covers', 'secondary_metric': 'second_usable',
        }
        output['complete_pairs'] = report['complete_pairs']
        output['pair_orders'] = numeric(report['pair_orders'])
        output['comparisons_ms'] = {k: numeric(report['comparisons_ms'][k]) for k in METRICS}
        output['pairs'] = [{
            'round': p['round'], 'order': p['order'], 'complete': p['complete'],
            **{side: {k: numeric(p[side][k]) for k in METRICS} for side in ('baseline_ms', 'candidate_ms', 'savings_ms')},
        } for p in report['pairs']]
    if 'short_sleep_visual' in report:
        output['short_sleep_visual'] = {
            side: {k: numeric(report['short_sleep_visual'][side][k]) for k in (
                'same_second_gui', 'active_input_handles', 'parked_input_handles', 'woken_input_handles',
            )} | {'visible_library_matches': True, 'right_toolbar_matches': True}
            for side in ('baseline', 'candidate')
        }
    return output


def main():
    need(sys.platform == 'linux', 'Run export verification through ssh test-env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    need(re.fullmatch(r'[0-9a-f]{64}', args.plan_sha256), 'Invalid plan digest')
    result = export(json.loads(args.input.read_text()), args.plan_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write('\n')
    print(json.dumps({k: result[k] for k in ('scope_name', 'valid', 'qualified')}))


if __name__ == '__main__':
    main()
