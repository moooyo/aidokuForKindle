"""Replay the isolated, source-bound cover prewarm observations (never a timing claim)."""

import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path


CASES=('disabled','hit-locked','changed-before','changed-after','missing-before','missing-after','cancelled','decoder-busy')
TEST='app::cover_jobs::qualified_prewarm_observation::controlled_primary_hint'
HIT='startup_cover_warm_hit'
USE='cover_local_derivative_memory_hit'


def require(value, message):
    if not value:raise ValueError(message)


def exercise(binary,out):
    out.mkdir(mode=0o700)
    failed=[]
    for mode in CASES:
        env=dict(os.environ,AIDOKU_TEST_PREWARM_CASE=mode,AIDOKU_TEST_PREWARM_OUT=str(out/(mode+'.json')))
        p=subprocess.run([binary,'--exact',TEST,'--test-threads=1','--nocapture'],env=env,cwd=out,
                         stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=40)
        (out/(mode+'.log')).write_bytes(p.stdout)
        if p.returncode or b'1 passed; 0 failed' not in p.stdout:failed.append(mode)
    require(not failed,'One or more controlled prewarm cases failed: '+', '.join(failed))
    return summarize(out)


def summarize(out):
    observations={}
    for mode in CASES:
        o=json.loads((out/(mode+'.observations.json')).read_bytes());t=json.loads((out/(mode+'.json')).read_bytes())
        require(t['success'] is True and t['finished'] is True and t['dropped_events']==0,'Incomplete application/cover trace: '+mode)
        events=[e['name'] for e in t['events']]; counters=Counter(events)
        require(o['case']==mode and o['offline'] is True,'Unqualified fixture or network policy')
        require(o['hint_selected'] is (mode!='disabled') and o['limit']==(0 if mode=='disabled' else 2), 'Wrong option or captured hint')
        require(len(o['library_busy_codes'])==2 and all(v in (5,6) for v in o['library_busy_codes']), 'No independently observed exclusive library lock')
        require(o['cache_valid_rows_while_locked']==[1,1] and o['library_rows_unlocked']==[1,1], 'Independent cache/initial Library availability missing')
        require(o['memory_entries_before_warm']==0 and o['warm_commits']==0 and o['artifacts_unchanged_during_warm'] is True and
                o['artifact_count_before_warm']==o['artifact_count_after_warm'], 'Cold setup or prewarm writes were observed')
        for key in ('initial_source_sha256','final_source_sha256','initial_derivative_sha256','final_derivative_sha256',
                    'initial_rgb_sha256','final_rgb_sha256','reference_rgb_sha256'):
            require(isinstance(o[key],str) and bool(re.fullmatch('[a-f0-9]{64}',o[key])), 'Missing measured content identity: '+key)
        require(o['final_rgb_sha256']==o['reference_rgb_sha256'] and o['width']>0 and o['height']>0 and
                o['rgb_bytes']==3*o['width']*o['height'], 'Final consumer produced different measured pixels: '+mode)
        changed=mode.startswith('changed-');hit=mode in ('hit-locked','changed-after','missing-after')
        require((o['initial_source_sha256']!=o['final_source_sha256'])==changed and
                (o['initial_rgb_sha256']!=o['final_rgb_sha256'])==changed and o['same_source_mtime'] is True and
                len(o['source_sizes'])==2 and o['source_sizes'][0]==o['source_sizes'][1], 'Mutable-source validity not established')
        require(counters[HIT]==int(hit) and counters[USE]==int(mode=='hit-locked') and
                bool(o['prefetch_consumed'])==(mode=='hit-locked'),'The planned prefetch was not consumed, or stale data was used: '+mode)
        require((o['memory_entries_after_warm']>0)==hit,'Prepared-pixel residency contradicts the trace')
        require(o['before_warm_derivative_sha256']==o['after_warm_derivative_sha256'] and
                (o['before_foreground_derivative_sha256'] is None)==mode.startswith('missing-'), 'Disk derivative changed unexpectedly')
        if mode=='hit-locked':
            seq=['startup_cover_warm_plan','startup_cover_warm_attempt',HIT,USE]
            require(all(counters[n]==1 for n in seq) and [events.index(n) for n in seq]==sorted(events.index(n) for n in seq)
                    and o['foreground_commits']==0 and o['foreground_local_derivative_hit'] is True,
                    'Positive cached pixels did not survive the fresh ordinary validation path')
        elif mode=='disabled':require(counters['startup_cover_warm_plan']==0 and counters['startup_cover_warm_attempt']==0,'Disabled option performed warm work')
        elif mode=='cancelled':require(counters['startup_cover_warm_stopped']>0 and counters['startup_cover_warm_attempt']==0,'Cancelled plan did work')
        if mode in ('changed-before','decoder-busy'):
            require(counters['startup_cover_warm_image_unavailable']==1,'A changed source or occupied decoder must skip prewarm')
        if mode=='missing-before':require(counters['startup_cover_warm_image_failed']==1,'A missing derivative was not declined')
        require(o['foreground_commits']==int(changed or mode.startswith('missing-')) and
                o['foreground_local_derivative_hit'] is not (changed or mode.startswith('missing-')),
                'The ordinary foreground accepted stale cover state or did unnecessary work')
        require(b'1 passed; 0 failed' in (out/(mode+'.log')).read_bytes(),'No completed host fixture')
        # Render keys include the canonical temporary directory; compare its
        # within-run byte relationships, not path-specific digests across runs.
        observations[mode]={'case':mode,'selected':o['hint_selected'],'library_reader_blocked':True,'cache_index_readable':True,
                            'source_replaced_without_metadata_change':changed,'warm_hits':counters[HIT],'ordinary_memory_hits':counters[USE],
                            'generated_during_prewarm':o['warm_commits'],'ordinary_commits':o['foreground_commits'],
                            'ordinary_matches_current_source_pixels':True,'synthetic_final_rgb_sha256':o['final_rgb_sha256']}
    stable={r['synthetic_final_rgb_sha256'] for mode,r in observations.items() if not mode.startswith('changed-')}
    replaced={r['synthetic_final_rgb_sha256'] for mode,r in observations.items() if mode.startswith('changed-')}
    require(len(stable)==len(replaced)==1 and stable.isdisjoint(replaced), 'Enabled/disabled or replacement fixture pixels disagree across cases')
    return observations
