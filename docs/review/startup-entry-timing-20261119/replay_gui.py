#!/usr/bin/env python3
"""Replay a *completed* normal-data six-visit native Library study, off-device.

On test-env: python3 -B replay_gui.py BATCH_DIR INSTALLED_PRIVATE_P6_ARCHIVE
Pass --pilot with its own separate, rejected batch for the non-counted observation.
Pass --low to replay the separate 75 ms/32-pixel-lattice cohort; never pool cohorts.
The JSON written to stdout contains relative timings and public checks only.
No device connection is opened; a partial, recovered or failed batch is not eligible.
"""

import argparse
import gzip
from hashlib import sha256
import io
import itertools
import json
from pathlib import Path
import re
from statistics import mean, median
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_analysis as af


class Rejected(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise Rejected(reason)


R3 = {
    'aidoku/app/aidoku': '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af',
    'aidoku/launch.sh': '412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675',
    'aidoku/app/aidoku-startup-portrait': 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50',
}
INPUTS = {
    'guitap': '2df118aad6346f8835224cabe50e27d56ddfe97a65a60ac5ff99ea707198f3c8',
    'framewatch': '229fe8c1973b2861f94786c4c88f044508920f433806c1ed2cdb5cc208c90be4',
    'capture': 'd728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90',
    'smoke-lib.sh': '6d37d4ca09dea6966a2df59cf7f7648c3d4da317c1106fa24cca3ad96682c0ae',
    'visit.sh': '5c7eb4c11fb7e6bdfda3ebce455d046a3052f2d551a17e31ed410f43ff8a36b8',
}
TARGET_PLAN = '13fa7f18827f4abb2002f91bd9ba2a4864693fd9ef667c80bd5efad9b0e0caec'
# Plan/result digests identify the read-only evidence selection, not user pixels.
FORMAL = ('5af0cd8b56f2d40d67f03510071ccbce6ecacac982f3a314debf15674d6f4b6b',
          '58e20384cf0ceaece7ef3ecdd232d64321f7b4c36401bccead902e913c43f5ae')
LIGHT = ('86f1ed1c4034a54546a2172e1cbd3e09feed61d9e783c1cfe11511e402b5b27f',
         'ad0bbb333cef7722587994291a2bbf020a16d8ccb349fcfe941acbe255bbf328')
PILOT = ('966924cc44d2963c6b8e28dfc8036fa5380e44bf04d17e63747b9aecb46a9817',
         '83b10f232eb33ca6adcf942cca541f91199ed96d3ce16af487302306aa049c55')
LIGHT_INPUTS = INPUTS | {'framewatch':'759f5a48316718f774853a768c4207f6a1d3ffc5b2378ac7e08b5282c2000b9c',
                         'visit.sh':'bf7903210b350347665b6d13467dec39b6b70579257dfae947a7e3ab8794f0ff'}
PREFIX = b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n'
SIZE = len(PREFIX) + af.W * af.H * 3
PER_CASE = {'frames.bin': 24 * 1024**2, 'settled.ppm.gz': SIZE + 65536, 'lifecycle.log': 1024**2,
            'framewatch.log': 4096, 'tap.tsv': 2048,
            **dict.fromkeys(('capture.ok', 'smoke.log.bound', 'smoke.native', 'smoke.request',
                             'smoke.access', 'smoke.catalog', 'owned-app', 'term.sent',
                             'ordinary-exit.ok', 'visit.ok'), 512)}

# Native geometry from ui/theme.slint, app.slint, library.slint on Scribe (2x).
# Six distinct books in a five-column grid, plus the separate continuing-book artwork.
CHROME = af.DEFAULT['chrome']
ART = [(80, 224, 152, 224)] + [(80 + (i % 5) * 348, 504 + round(i // 5 * 542.88), 308, 419) for i in range(6)]
# Use the text-bearing caption rows without touching artwork or the system status bar.
TEXT = CHROME + [(272, 234, 1030, 144), (72, 936, 1718, 92), (72, 1479, 324, 92)]
REGIONS = {'library_chrome': CHROME, 'book_text_and_navigation': TEXT,
           'all_seven_cover_slots': ART, 'settled_body': af.DEFAULT['body']}


def load_bytes(path, maximum):
    need(not path.is_symlink() and path.is_file() and path.stat().st_size <= maximum, 'invalid_private_input')
    return path.read_bytes()


def unique_json(raw):
    def pairs(xs):
        d = dict(xs)
        need(len(d) == len(xs), 'duplicate_json_field')
        return d
    return json.loads(raw, object_pairs_hook=pairs)


def read_members(path, allowed, required):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= sum(allowed.values()) + 1024**2,
         'unsafe_archive')
    result = {}
    with tarfile.open(path, 'r:') as tar:
        for item in tar:
            need(item.name in allowed and item.name not in result and
                 item.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not item.sparse and
                 not any('sparse' in k.lower() for k in item.pax_headers) and
                 not item.linkname and 0 <= item.size <= allowed[item.name], 'unexpected_archive_member')
            with tar.extractfile(item) as stream:
                result[item.name] = stream.read(item.size + 1)
            need(len(result[item.name]) == item.size, 'truncated_archive_member')
    need(required <= result.keys(), 'missing_archive_member')
    return result


def uncompress(blob, maximum):
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
        data = stream.read(maximum + 1)
    need(len(data) <= maximum, 'decompressed_input_too_large')
    return data


def capture(gz):
    data = uncompress(gz, SIZE)
    need(len(data) == SIZE and data.startswith(PREFIX), 'wrong_independent_capture_layout')
    return data[len(PREFIX):]


def reference(path, lattice=(16,8)):
    allowed = {label + suffix: (SIZE + 65536 if suffix == '.native.ppm.gz' else 256)
               for label in ('before', 'after', 'wake') for suffix in ('.native.ppm.gz', '.sha', '.right.tsv')}
    members = read_members(path, allowed, set(allowed))
    body = capture(members['after.native.ppm.gz']); skip = 3 * af.W * 72
    for k in ('before', 'after', 'wake'):
        need(re.fullmatch(rb'[0-9a-f]{64}\n', members[k+'.sha']) and
             sha256(body[skip:]).hexdigest().encode() + b'\n' == members[k+'.sha'], 'stored_reference_body_differs')
    result = af.reference_grid(path, 3, lattice)
    return memoryview(body)[skip:], result


def pick_tap(data):
    t = unique_json(data)
    need(t['mode'] == 'tap' and t['clock'] == 'CLOCK_BOOTTIME' and t['hold_requested_ms'] == 50 and
         all(t[k] is True for k in ('down_confirmed', 'up_sent', 'buttons_clear_after', 'ok')), 'input_not_confirmed')
    times = [t[n + '_us'] for n in ('down_before', 'down_after', 'up_before', 'up_after')]
    need(all(type(v) is int and v > 0 for v in times) and times == sorted(times) and
         times[2] - times[1] >= 50000, 'bad_button_clock_or_hold')
    return {'down': tuple(times[:2]), 'up': tuple(times[2:])}


def subtract(end, start):
    return end[0] - start[1], end[1] - start[0]


def born(ticks, hz):
    need(type(ticks) is int and ticks > 0 and type(hz) is int and hz > 0, 'bad_process_clock')
    return ticks * 1000000 // hz, -(-((ticks + 1) * 1000000) // hz)


def ms(n):
    return round(n / 1000, 3)


def pair_ms(bounds):
    return list(map(ms, bounds))


def qs(pairs):
    return {'lower_ms': ms(median(a for a, _ in pairs)), 'upper_ms': ms(median(b for _, b in pairs))}


def measure(data, tap, grid, period, lattice=(16,8)):
    cfg, samples, (start, end, misses, cpu) = af.read_trace(data, lattice)
    need(cfg['period_ms'] == period and cfg['duration_ms'] == 12000 and cfg['rotation'] == 3 and
         cfg['stride'] == 1872 and cfg['xoffset'] == cfg['yoffset'] == 0, 'sample_configuration_changed')
    need(start < tap['down'][0] and tap['up'][1] < end and
         samples[0][1] < tap['down'][0] and samples[-1][0] > tap['up'][1], 'tap_not_in_observed_window')
    cap = max(100000, 3 * period * 1000)
    # mark returns conservative brackets relative to the input interval. Convert
    # to absolute bounds internally so all disjoint stages share the same edges.
    def edge(group, name):
        lo, hi = tap['down']
        mark = af.mark(samples, af.rows(group,lattice), grid, lo, hi, cap, 'chrome' if name in
                       ('library_chrome', 'book_text_and_navigation') else 'body')
        need(mark['lower_ms'] > 0, 'censored_onset')
        return round(mark['lower_ms'] * 1000) + hi, round(mark['upper_ms'] * 1000) + lo

    edges = {k: edge(v, k) for k, v in REGIONS.items()}
    slots = [edge([region], 'art') for region in ART]
    edges['first_final_cover_slot'] = min(v[0] for v in slots), min(v[1] for v in slots)
    edges['complete_library'] = (max(edges[k][0] for k in ('all_seven_cover_slots', 'book_text_and_navigation')),
                                 max(edges[k][1] for k in ('all_seven_cover_slots', 'book_text_and_navigation')))
    # Aggregate full-screen scans are not atomically instantaneous. Do not rank
    # edges more precisely than their observed last-mismatch/first-match bounds.
    durations = [hi-lo for lo, hi, _ in samples]
    gaps = [b[0]-a[1] for a, b in itertools.pairwise(samples)]
    stats = {'period_ms': period, 'observations': len(samples), 'skipped_slots': misses,
             'cpu_pct_one_core': round(100 * cpu / (end-start), 3), 'median_scan_ms': ms(median(durations)),
             'max_scan_ms': ms(max(durations)), 'max_gap_ms': ms(max(gaps)),
             'max_frame_edge_bracket_ms': ms(max(b-a for a,b in edges.values())),
             'max_endpoint_bracket_ms': ms(max(b-a for a,b in edges.values()) + max(b-a for a,b in tap.values()))}
    return edges, stats


def accepted_case(path, hz, ref_body, grid, period, lattice):
    m = read_members(path, PER_CASE, set(PER_CASE))
    need(all(m[k] == b'yes\n' for k in ('capture.ok', 'term.sent', 'ordinary-exit.ok', 'visit.ok')) and
         m['framewatch.log'] == b'' and m['smoke.native'] == b'com.lab126.KPPMainApp\n' and
         m['smoke.request'] == b'before\n', 'missing_normal_visit_or_exit_receipt')
    need(re.fullmatch(rb'[0-9a-f]{64}\n', m['smoke.catalog']) and re.fullmatch(rb'[0-9]+\n', m['smoke.access']),
         'invalid_catalog_binding')
    fields = m['owned-app'].strip().split()
    need(len(fields) == 7 and all(re.fullmatch(rb'[1-9][0-9]{0,20}', s) for s in fields[:6]) and
         re.fullmatch(rb'[0-9a-f]{32}', fields[6]), 'unbound_process_birth')
    pid, bt, rp, rt, wp, wt = map(int, fields[:6]); need(wt <= rt <= bt and len({pid,rp,wp}) == 3, 'invalid_process_order')
    log = m['lifecycle.log'].splitlines()
    need(log.count(f'aidoku: Launcher worker started (pid={wp}).'.encode()) == 1 and
         sum(x.startswith(b'aidoku: Launcher worker started ') for x in log) == 1 and
         sum(x.startswith(b'aidoku: native window ready; profile=scribe, logical=930x1240, scale=2') for x in log) == 1 and
         log.count(b'aidoku: Launcher worker exited with status 0.') == 1 and
         log.count(b'aidoku: Catalog notification completed.') == 1 and
         not any(b'Catalog notification unavailable' in x or b'standard reindex recovery' in x for x in log), 'bad_normal_lifecycle')
    body = capture(m['settled.ppm.gz'])
    need(memoryview(body)[3 * af.W * 72:] == ref_body, 'full_body_not_installed_library')
    tap = pick_tap(m['tap.tsv']); coords = unique_json(m['tap.tsv'])
    edges, stats = measure(m['frames.bin'], tap, grid, period,lattice)
    bounds = {'down': tap['down'], 'worker_process_birth': born(wt,hz), 'gui_process_birth': born(bt,hz), **edges}
    chain = ('down', 'worker_process_birth', 'gui_process_birth', 'library_chrome', 'complete_library')
    stages = {}
    for a, b in itertools.pairwise(chain):
        interval = subtract(bounds[b], bounds[a]); need(interval[0] >= 0, 'disjoint_boundaries_unordered'); stages[a + '_to_' + b] = interval
    timings = {base: {k: subtract(v, ts) for k,v in edges.items()} for base,ts in tap.items()}
    return {'timings':timings, 'stages':stages, 'stats':stats, 'button_hold':subtract(tap['up'],tap['down']),
            'coords':(coords['x'],coords['y']), 'catalog':m['smoke.catalog'], 'birth':bt, 'nonce':fields[6], 'worker':wt,
            'art_equals_body':edges['all_seven_cover_slots']==edges['settled_body']}


def locate(batch_root, shot, point, target):
    """Independently match the clicked text/logo patch at the unique frozen grid position."""
    import numpy as np
    def image(p):
        data = uncompress(load_bytes(p, 8*1024**2), 1872*2480)
        need(len(data) >= 1872*2480, 'native_view_truncated')
        return np.frombuffer(data, np.uint8, count=1872*2480).reshape(2480,1872)[:,:1860]
    source_path = Path(target['reference'])
    need(source_path.resolve().is_relative_to(batch_root.resolve()/'private'), 'unknown_target_reference')
    source = image(source_path); cur=image(shot)
    x,y,w,h=target['tile_text']; key=source[y:y+h,x:x+w]; ink=key<130
    need(np.mean(key < 150) > .02 and np.std(key) > 15, 'uninformative_target')
    matches=[]
    for ax,ay in target['anchors']:
        top_score, top_point = -1, None
        for oy in range(-4,5):
            for ox in range(-15,16):
                left,top = ax+ox,ay+oy
                view=cur[top:top+h,left:left+w]
                if view.shape != key.shape: continue
                dark = view < 130
                score=min(np.mean(np.abs(view.astype('int16')-key)<20), np.count_nonzero(dark & ink)/max(1,np.count_nonzero(dark | ink)))
                if score > top_score: top_score, top_point=score,(left+w//2,top+h//2)
        if top_score >= .970: matches.append(top_point)
    need(matches == [point], 'native_tile_not_uniquely_identified')


def bound_plan(batch, expected):
    raw = [load_bytes(batch/k,65536) for k in ('plan.json','result.json')]
    need(tuple(sha256(r).hexdigest() for r in raw) == expected, 'not_the_frozen_study_manifest')
    return tuple(unique_json(r) for r in raw)


def validated_run(batch, low):
    root = batch.parent.parent
    need(batch.is_dir() and not batch.is_symlink() and batch.parent.name == 'private', 'unexpected_batch_root')
    plan, result = bound_plan(batch, LIGHT if low else FORMAL)
    need(plan['batch'] == result['batch'] == batch.name and re.fullmatch(r'entry-time-[0-9a-f]{12}', batch.name), 'wrong_run_binding')
    need(plan['mode'] == result['mode'] == 'time' and plan['method'] == result['method'] == 'x' and
         plan['planned'] == result['planned'] == result['passed_cases'] == 6 and plan['passed_cases'] == 0 and
         plan['lease_released'] is False and result['lease_released'] is True and result['operation_succeeded'] is True and
         result['original_state_restored_or_unmodified'] is True and not result.get('protected_identity_failed') and
         not result.get('error') and not result.get('cleanup_errors'), 'partial_or_failed_batch')
    need(plan['inputs'] == (LIGHT_INPUTS if low else INPUTS) and
         plan.get('low_observer',False) is low and result.get('low_observer',False) is low, 'frozen_device_inputs_changed')
    need(unique_json(load_bytes(root/'release-programs.json',4096)) == R3, 'wrong_installed_release')
    raw_target = load_bytes(root/'target-plan.json',32768)
    need(sha256(raw_target).hexdigest() == TARGET_PLAN, 'wrong_frozen_native_target')
    ops = result['operations']; names = [o['operation'] for o in ops]
    need(len(names) == len(set(names)) and all(o['exit'] == 0 and o['timeout'] is False for o in ops), 'unsuccessful_operation')
    def op(name):
        need(name in names, 'missing_recorded_operation')
        found = list(batch.glob('[0-9]*-' + name + '.stdout'))
        need(len(found) == 1 and int(found[0].name.split('-',1)[0]) == names.index(name), 'operation_receipt_mismatch')
        return found[0]
    sequence = ['native-before','lights-before','release-registration','check-inputs','clock-tick','library-view-private']
    for i in range(1,7): sequence += [f'case-dir-{i}', f'visit-{i}', f'collect-{i}']
    sequence += ['native-view-owner','restore-pointer','light-after-restore','remove-stage','native-after','release-registration-after']
    if low:
        need(all('cache-roster-'+str(i) in names for i in range(7)), 'missing_outside_window_observation')
        for i in range(1,7):
            need(names.index('cache-roster-'+str(i-1)) < names.index('visit-'+str(i)) < names.index('cache-roster-'+str(i)),
                 'cache_count_inside_window')
    for k in sequence: op(k)
    need([names.index(k) for k in sequence] == sorted(names.index(k) for k in sequence), 'wrong_operation_order')
    need(load_bytes(op('release-registration'),65536) == load_bytes(op('release-registration-after'),65536) == b'' and
         load_bytes(op('check-inputs'),65536) == b'' and
         load_bytes(op('clock-tick'),256).strip() == b'100' and
         load_bytes(op('native-before'),65536) == load_bytes(op('native-after'),65536) and
         load_bytes(op('lights-before'),4096) == load_bytes(op('light-after-restore'),4096), 'release_state_or_tick_guard_failed')
    acquired, released = [unique_json(load_bytes(batch/('lease-'+n+'.json'),8192)) for n in ('acquired','released')]
    need(acquired['ok'] is True and released['ok'] is True and acquired['run_id'] == released['run_id'] == batch.name and
         all(acquired[k] == released[k] for k in ('project','owner_thread','lease_token_sha256')) and
         all(released[k] is True for k in ('owner_and_token_checked','native_restored_and_connections_closed',
            'only_owned_owner_json_removed_then_rmdir','lease_directory_absent_after_release')), 'lease_release_not_confirmed')
    return root, op, names, unique_json(raw_target)


def replay(batch, installed, low=False):
    root, op, names, target = validated_run(batch,low)
    period,lattice=(75,(32,16)) if low else (25,(16,8))
    body, grid = reference(installed,lattice)
    rows=[]
    for i in range(1,7):
        need(load_bytes(op('visit-'+str(i)),512) == b'VISIT_OK\n', 'invalid_visit_completion')
        row = accepted_case(op('collect-'+str(i)), 100, body, grid, period,lattice)
        shot = 'library-view-private' if i == 1 else 'library-before-'+str(i) if 'library-before-'+str(i) in names else 'view-before-'+str(i)
        locate(root,op(shot),row['coords'],target); rows.append(row)
    need(len({r['birth'] for r in rows}) == len({r['worker'] for r in rows}) == len({r['nonce'] for r in rows}) == 6 and
         len({r['catalog'] for r in rows}) == 1, 'visits_not_independent_or_catalog_changed')
    def public(r,i):
        return {'visit':i, 'endpoint_ranges_ms':{base:{k:pair_ms(v) for k,v in endpoints.items()} for base,endpoints in r['timings'].items()},
                'disjoint_ranges_ms':{k:pair_ms(v) for k,v in r['stages'].items()}, 'press_to_release_ms':pair_ms(r['button_hold']),
                'observer':r['stats'], 'artwork_edge_equals_settled_body':r['art_equals_body']}
    summary = {base:{k:qs([r['timings'][base][k] for r in rows]) for k in rows[0]['timings'][base]} for base in ('down','up')}
    budgets = {k:qs([r['stages'][k] for r in rows]) for k in rows[0]['stages']}
    summed_mean = {k: ms(mean((r['stages'][k][0]+r['stages'][k][1])/2 for r in rows)) for k in budgets}
    report={'schema':1, 'release':'Installed R3, product 8baf57f, Scribe; normal user data. The separate preflight inventory found no saved startup-options file.',
            'visits_planned':6, 'visits_accepted':6,'partial_pilots_included':0,'input':'Native X11/XTest Library tile, verified button down and up, requested 50 ms hold.',
            'method':['Independent post-visit full capture equals qualified installed R3 reference below the live status bar on every visit. No Library file, launcher, data or cache is substituted for measurement.',
                      'First layout: exact sustained category bar plus bottom navigation. Text/navigation also includes the continuing-book title and both populated caption rows. All artwork: continuing book plus six grid slots. Complete Library: both all artwork and text/navigation. Whole settled body is measured separately.',
                      'Same-device CLOCK_BOOTTIME. Conservative read brackets run from previous mismatching scan start to first matching scan end; the reference must remain confirmed for at least 200 ms. Each record is a sparse framebuffer observation, not an interactive-readiness, EPDC ACK or optical measurement.',
                      'Native tile recognition, three-file release assertion, unchanged native snapshot/light, scoped normal exits, unique new process lineage, recent-access catalog checks and owner/token-checked release are required. No partial pilot contributes.',
                      'Process births are 10 ms /proc stat bins at the observed 100 USER_HZ. A birth is not the first Rust instruction. Disjoint medians need not sum; reported means use interval midpoints and are approximate. Per-book cache hits/misses were not instrumented on these ordinary installed launches. No observer bias is subtracted.'],
            'median_endpoint_bounds_ms':summary, 'median_disjoint_bounds_ms':budgets, 'mean_disjoint_midpoints_ms':summed_mean,
            'observer':{'period_ms':period,'sparse_points_per_scan':len(grid), 'all_artwork_sample_points':sum(sum(b-a for a,b in spans) for spans in af.rows(ART,lattice)),
                        'median_cpu_pct_one_core':median(r['stats']['cpu_pct_one_core'] for r in rows),
                        'max_cpu_pct_one_core':max(r['stats']['cpu_pct_one_core'] for r in rows),
                        'max_gap_ms':max(r['stats']['max_gap_ms'] for r in rows),
                        'max_endpoint_bracket_ms':max(r['stats']['max_endpoint_bracket_ms'] for r in rows)},
            'artwork_edge_equals_whole_body_visits':sum(r['art_equals_body'] for r in rows),'samples':[public(r,i) for i,r in enumerate(rows,1)]}
    if low:
        report['method'].append('This is a separate lighter observer cohort. Do not pool it with the 25 ms cohort or infer causal observer bias from the difference. Cache inventories ran outside each timing window.')
    return report


def pilot(batch, installed):
    plan,res=bound_plan(batch, PILOT); root=batch.parent.parent
    old_inputs=INPUTS | {'visit.sh':'d1d1dcf7615258ccc9dd4183c34fb4ea2811cb4e20162f287540f721af83f4b7'}
    need(plan['batch']==res['batch']==batch.name and plan['inputs']==old_inputs and
         res['method']=='x' and res['planned']==6 and res['passed_cases']==0 and res['operation_succeeded'] is False and
         res['original_state_restored_or_unmodified'] is True and res['lease_released'] is True, 'not_the_excluded_pilot')
    def file(name, suffix='stdout'):
        hits=list(batch.glob('[0-9]*-'+name+'.'+suffix)); need(len(hits)==1,'missing_pilot_operation'); return hits[0]
    seen={o['operation']:o for o in res['operations']}
    need(seen['visit-1']['exit']!=0 and not seen['visit-1']['timeout'] and seen['recover-case']['exit']==0 and
         load_bytes(file('recover-case'),512)==b'VISIT_RECOVERED\n' and
         b'Visit refused: stale ordinary exit epoch' in load_bytes(file('visit-1','stderr'),4096) and
         load_bytes(file('native-before'),65536)==load_bytes(file('native-after'),65536) and
         seen['release-registration']['exit']==0 and unique_json(load_bytes(root/'release-programs.json',4096))==R3,
         'missing_pilot_recovery_or_registration')
    release=unique_json(load_bytes(batch/'lease-released.json',8192))
    need(release['run_id']==batch.name and release['owner_and_token_checked'] is True and release['lease_directory_absent_after_release'] is True,
         'missing_pilot_release')
    missing={'visit.ok','ordinary-exit.ok','term.sent','lifecycle.log'}; remaining=set(PER_CASE)-missing
    m=read_members(file('partial-private-case'), {k:PER_CASE[k] for k in remaining}, remaining)
    ref_body,grid=reference(installed)
    need(memoryview(capture(m['settled.ppm.gz']))[3*af.W*72:]==ref_body and m['capture.ok']==b'yes\n', 'pilot_reference_differs')
    tap=pick_tap(m['tap.tsv']); edges,stats=measure(m['frames.bin'],tap,grid,25)
    return {'schema':1,'kind':'excluded_pilot_not_a_primary_observation','primary_visits_contributed':0,
            'reason':'The original harness rejected a fresh never-suspended epoch before formal exit verification. Separate owned recovery restored native state and released the lease. This pilot is not interchangeable with a successful qualification.',
            'capture':'Complete captured body below the status bar equals the independently retained installed R3 reference.',
            'last_artwork_edge_equals_body':edges['all_seven_cover_slots']==edges['settled_body'],
            'down_ranges_ms':{k:pair_ms(subtract(v,tap['down'])) for k,v in edges.items()},
            'up_ranges_ms':{k:pair_ms(subtract(v,tap['up'])) for k,v in edges.items()}, 'observer':stats,
            'causality':'No per-cover storage, provider or scheduler trace was collected; the extra delay cannot be assigned to a cache miss, a network fetch or any other specific cause.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('batch',type=Path); ap.add_argument('installed_reference',type=Path)
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--pilot',action='store_true',help='Only replay the separate failed, recovered pilot; never combine it with formal samples.')
    group.add_argument('--low',action='store_true',help='Only replay the separate 75 ms light observer cohort.')
    args=ap.parse_args()
    try:
        report=pilot(args.batch,args.installed_reference) if args.pilot else replay(args.batch,args.installed_reference,args.low)
    except (Rejected,af.Invalid) as err:
        sys.exit('Replay rejected: ' + str(err))
    except (ValueError, KeyError, OSError, UnicodeError, EOFError, tarfile.TarError):
        sys.exit('Replay rejected: malformed or unreadable private evidence')
    print(json.dumps(report,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
