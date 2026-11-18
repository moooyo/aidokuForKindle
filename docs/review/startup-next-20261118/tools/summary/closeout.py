"""Bind the completed public performance index to the installed application."""

import argparse
import json
from pathlib import Path
from statistics import median


ELF = "9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f"
SOURCE = "1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768"


def load(path):
    return json.loads(path.read_text())


def save(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def samples(report, count):
    require(report['valid'] and report['artifacts']['candidate']['application_sha256'] == ELF
            and report['mechanism']['verified'], 'Invalid final candidate evidence')
    rows = {r['case']: r for b in report['batches'] for r in b['cases'] if r['case'][-2:].isdecimal()}
    require(len(rows) == 2 * count and all(r['status'] == 'ok' for r in rows.values()), 'Incomplete sample')
    return [[rows[f'{side}-{i:02}'] for i in range(1, count + 1)] for side in ('baseline', 'candidate')]


def med(values):
    return round(median(values), 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='Directory already populated by refresh.sh')
    parser.add_argument('--reports', type=Path, required=True, help='Final combined status analyzer reports')
    parser.add_argument('--release', type=Path, required=True, help='Public qualified release inputs')
    args = parser.parse_args()
    index = load(args.out / 'index.json')
    require(all(index['slots'][k]['status'] == 'completed' for k in
                ('status-warm-guard', 'status-cold-screen', 'status-cold-confirm', 'status-pixels')),
            'Incomplete performance or pixel export')
    require(index['slots']['cold-deployment']['status'] == 'not_run_after_warm_guard',
            'The rejected standalone cold candidate must stay separate')

    life = load(args.release / 'lifecycle.json')
    deploy = load(args.release / 'deployment.json')
    require(life['source_manifest_sha256'] == SOURCE and life['artifact_sha256'] == {'aidoku/app/aidoku': ELF}
            and all(life['checks'][k] for k in ('right_status_bar', 'pixels', 'input', 'exit', 'sleep_wake',
                                                'notification', 'catalog_identity', 'restoration', 'production_defaults')),
            'Default lifecycle proof differs')
    require(deploy['source_sha256'] == SOURCE and deploy['application_sha256'] == ELF
            and deploy['only_changed_program_member'] == 'aidoku/app/aidoku' and deploy['zip_checks_passed'] == 211
            and all(deploy[k] for k in ('success', 'lease_released', 'backup_retained', 'native_and_lighting_restored',
                'original_shared_registration_preserved', 'installed_default_document', 'same_process_short_sleep_wake',
                'pixels_match_before_after_and_wake', 'matching_observable_right_toolbar', 'catalog_notifications_complete')),
            'Normal installed application was not qualified')
    for slot, data, name in [('status-lifecycle', life, 'default-lifecycle.json'),
                             ('status-deployment', deploy, 'deployment.json')]:
        save(args.out / name, data)
        index['slots'][slot] = {'status': 'completed', 'run_ids': [data['run_id']], 'gate_passed': True,
                                'application_sha256': ELF, 'evidence': name, 'timing_sample': False}

    cold = load(args.reports / 'status-cold-confirm.json')
    warm = load(args.reports / 'status-warm-guard.json')
    require(cold['latency_gate_passed'] and warm['warm_guard_passed'], 'Timing gates failed')
    old, new = samples(cold, 10)
    _, warmed = samples(warm, 6)
    jobs = [[row['cold']['jobs'] for row in group] for group in (old, new)]
    for group in jobs:
        require(all([j['ordinal'] for j in jobset] == list(range(1, 7)) for jobset in group), 'Changed fixture order')
    totals = [sum(job['duration_ms'] for job in j) for j in jobs[1]]
    tails = [row['diagnostics']['cover_tail_ms'] for row in new]
    result = {
        'application_sha256': ELF, 'cold_run': cold['batches'][0]['run_id'], 'warm_run': warm['batches'][0]['run_id'],
        'scope': 'Elapsed intervals, not CPU profiles or guaranteed improvements. Ordinals are the anonymous order of six serial local cover tasks.',
        'cold_job_medians_ms': [{'ordinal': i + 1, 'baseline_ms': med([j[i]['duration_ms'] for j in jobs[0]]),
                                 'candidate_ms': med([j[i]['duration_ms'] for j in jobs[1]]),
                                 'median_paired_savings_ms': med([a[i]['duration_ms'] - b[i]['duration_ms'] for a,b in zip(*jobs)])}
                                for i in range(6)],
        'cold_candidate_median_total_job_ms': med(totals), 'cold_candidate_median_tail_ms': med(tails),
        'cold_candidate_median_job_share_of_tail': round(median(t / tail for t, tail in zip(totals, tails)), 4),
        'warm_candidate_median_tail_ms': med([r['diagnostics']['cover_tail_ms'] for r in warmed]),
        'warm_candidate_median_first_render_ms': med([r['diagnostics']['first_frame_cost_ms'][0] for r in warmed]),
        'cost_note': 'The combined decoder markers also include transformations and source validation. Separate image reads, inflate, resize, dither and persistence before changing algorithms.'}
    save(args.out / 'remaining-costs.json', result)
    index['remaining_costs'] = 'remaining-costs.json'
    save(args.out / 'index.json', index)


if __name__ == '__main__':
    main()
