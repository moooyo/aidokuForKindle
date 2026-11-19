#!/usr/bin/env python3
"""Analyze paired Library reopens on test-env, outside the device lease."""

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
import sys
from types import ModuleType


WARM = 'f796f2c20c75f183996d63612c19dad7a4a8aa47e7ce0f1d0995124509bdc8c7'
RUNNER = '2ea29b78c044ed2884765a0ca7c6993e244eed53d937779998573a7a90345226'
DEVICE = 'c8bbdad79863a7785c3cfd41c07662dec011f60bb3c5971745be4a727e318b21'
ORIGINAL_LAUNCHER = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
DEPLOYED = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
SIDES = ('baseline', 'candidate')
DIGEST = re.compile(r'[a-f0-9]{64}\Z')
PHASES = ('pilot', 'screen', 'confirm', 'qualify')
SCENARIOS = ('immediate', 'ttl', 'loss', 'ordinary')
SAMPLING = ('between','pre_second','after_second')
PUBLIC_CASE = re.compile(r'(baseline|candidate)-(warmup|qualify|[0-9]{2})\Z')
OPTION_VALUES = {'portrait': {'shell','native'}, 'prewarm': {'0','2'}, 'last_cover_grace': {'off','on'},
                 'import_thumbnail': {'idle','off'}, 'first_layout': {'baseline','optimized'}, 'resident': {'off','45'}}


def reference():
    paths = (Path(__file__).resolve().parents[3] / 'startup-next-20261118/tools/analyze_bench.py',
             Path('/root/aidoku-next-bench-20261118/tools/analyze_bench.py'))
    for path in paths:
        if path.is_file():
            data = path.read_bytes()
            if sha256(data).hexdigest() != WARM:
                raise ValueError('Prior measurement definitions do not match the reviewed source')
            mod = ModuleType('_open_measurements'); mod.__file__ = str(path)
            exec(compile(data, str(path), 'exec'), mod.__dict__)
            return mod
    raise ValueError('Missing prior measurement definitions')


REF = reference()
check = REF.require
Invalid = REF.Invalid


def textfile(path, limit=2048):
    try:
        with path.open('rb') as f:
            value = f.read(limit + 1)
        check(0 < len(value) <= limit and value.endswith(b'\n'), 'missing_or_malformed_receipt')
        return value.decode('ascii')
    except (OSError, UnicodeError):
        raise Invalid('missing_or_malformed_receipt') from None


@dataclass(frozen=True)
class OpenClock:
    """Private absolute boot clock anchors; never serialize them in a public dataset."""

    request: int
    first: int
    usable: int
    covers: int
    shutdown: int
    home: int

    @classmethod
    def read(cls, directory):
        shell = REF.marks(directory / 'shell.tsv')
        ev = REF.read_json(directory / 'application.json')['events']
        def at(name):
            values = [e.get('boottime_us') for e in ev if e.get('name') == name]
            check(len(values) == 1 and REF.natural(values[0]), 'missing_or_duplicate_clock')
            return values[0]
        first = at(REF.EVENTS['first'])
        stamp = cls(shell['native_open_requested'], first, max(first, at(REF.EVENTS['input_ready'])),
                    at(REF.EVENTS['covers']), at('shutdown_started'), shell['native_home_active'])
        check(stamp.request <= stamp.first <= stamp.usable <= stamp.shutdown <= stamp.home
              and stamp.first <= stamp.covers <= stamp.shutdown, 'invalid_open_clock_order')
        return stamp

    def stages(self):
        return {'first': self.first - self.request, 'usable': self.usable - self.request,
                'covers': self.covers - self.request, 'exit': self.home - self.shutdown,
                'post_covers_dwell': self.shutdown - self.covers, 'request_to_home': self.home - self.request}


def elapsed(prime, second):
    check(prime.home <= second.request, 'two_opens_overlap')
    gap = second.request - prime.home
    duration = {f'{name}_{key}': value for name, leg in (('prime', prime), ('second', second))
                for key, value in leg.stages().items()}
    duration.update(home_gap=gap, both_active_until_covers=duration['prime_covers'] + duration['second_covers'],
                    both_exit_cost=duration['prime_exit'] + duration['second_exit'],
                    first_request_to_second_covers=second.covers - prime.request,
                    first_request_to_second_home=second.home - prime.request)
    # The decomposition includes the deliberate in-app dwell, any between-open
    # stabilization or measured resource observation, and both native exits.
    check(duration['prime_covers'] + duration['prime_post_covers_dwell'] + duration['prime_exit'] + gap
          + duration['second_covers'] == duration['first_request_to_second_covers'], 'span_accounting')
    check(duration['first_request_to_second_covers'] + duration['second_post_covers_dwell']
          + duration['second_exit'] == duration['first_request_to_second_home'], 'wall_accounting')
    return {k: REF.ms(v) for k,v in duration.items()}


def sample_stats(values):
    return {'n': len(values), 'min': min(values), 'median': round(median(values), 3), 'max': max(values)} if values else None


def unsigned(value, limit):
    check(re.fullmatch(r'0|[1-9][0-9]{0,15}', value) and int(value) <= limit, 'invalid_integer_receipt')
    return int(value)


def audit_leg(directory, primary):
    check(textfile(directory/'case-ok') == 'ok\n', 'missing_case_receipt')
    data = REF.trace_case(directory,primary)
    REF.check_operating_evidence(directory, data['diagnostics'])
    return OpenClock.read(directory), data


def link_catalog(prime, second):
    # Raw identity/thumbnail bytes and access timestamps never leave the host.
    first_row = (prime/'catalog-after.identity').read_bytes()
    second_row = (second/'catalog-before.identity').read_bytes()
    first_access = (prime/'catalog-after.access').read_bytes()
    second_access = (second/'catalog-before.access').read_bytes()
    check(first_row and first_row == second_row and first_access == second_access, 'catalog_changed_between_opens')


def identities(plan, expected_sha):
    raw=plan.get('variants');check(isinstance(raw,dict) and set(raw)==set(SIDES), 'invalid_variants')
    known={'application_sha256','native_query_sha256','launcher_sha256','options_sha256','variant_sha256','startup_options','extra_sha256','args','env'}
    result={}
    for side in SIDES:
        v=raw[side];check(isinstance(v,dict) and set(v)==known, 'unrecognized_variant_shape')
        for key in ('application_sha256','native_query_sha256','launcher_sha256','options_sha256','variant_sha256'):
            check(isinstance(v[key],str) and DIGEST.fullmatch(v[key]),'invalid_artifact_identity')
        check(v['native_query_sha256']==QUERY and v['application_sha256']==expected_sha!=DEPLOYED,'untested_or_legacy_reopen_program')
        opts=v['startup_options'];check(isinstance(opts,dict) and all(k in OPTION_VALUES and isinstance(x,str) and x in OPTION_VALUES[k] for k,x in opts.items()), 'invalid_option_values')
        encoded=''.join(f'{k}={opts[k]}\n' for k in sorted(opts)).encode('ascii')
        check(sha256(encoded).hexdigest()==v['options_sha256'],'incorrect_options_digest')
        check(v['env'] in ({},{'AIDOKU_STARTUP_PORTRAIT':'native'},{'AIDOKU_STARTUP_PORTRAIT':'legacy'}) and v['args']==[], 'private_or_unrecognized_variant_options')
        extra=v['extra_sha256'];check(isinstance(extra,dict) and all(k=='aidoku-startup-portrait' and isinstance(h,str) and DIGEST.fullmatch(h) for k,h in extra.items()), 'unrecognized_helper')
        normalized={k:z for k,z in v.items() if k!='variant_sha256'}
        check(sha256(json.dumps(normalized,sort_keys=True,separators=(',',':')).encode()).hexdigest()==v['variant_sha256'], 'configuration_not_bound_to_variant')
        result[side]=dict(v)
    a,b=(result[s] for s in SIDES)
    check(all(a[k]==b[k] for k in ('application_sha256','native_query_sha256','launcher_sha256','args','env','extra_sha256')), 'different_executables_or_settings')
    opts=[dict(v['startup_options']) for v in (a,b)]
    check(opts[0].pop('resident','off')=='off' and opts[1].pop('resident',None)=='45' and opts[0]==opts[1], 'not_an_isolated_retention_comparison')
    return result


def audited_manifest(root, phase, expected_sha):
    p, r = (REF.read_json(root / name) for name in ('plan.json', 'report.json'))
    run = p.get('run_id')
    check(isinstance(run,str) and re.fullmatch(r'aidoku-all-[a-z0-9-]{1,48}',run), 'invalid_run_identity')
    check(p.get('mode') == phase, 'unexpected_measurement_phase')
    bind = ('run_id','cases','mode','variants','device_script_sha256','runner_sha256','payload_sha256',
            'reopen_policy','reopen_scenario','reopen_action_ms','baseline_reference_sha256',
            'cache_regime','fixture_policy','trial','primary','original_launcher_sha256')
    check(all(k in p and p[k] == r.get(k) for k in bind), 'frozen_plan_mismatch')
    check(p['baseline_reference_sha256']==DEPLOYED and p['reopen_policy']=='data-only-45-v1'
          and p['reopen_scenario'] in SCENARIOS and p['reopen_action_ms']==6000, 'incorrect_reopen_workload')
    check(phase not in ('screen','confirm') or p['reopen_scenario']=='immediate','failure_qualification_in_timing_sample')
    check((p['trial'],p['primary'],p['cache_regime'],p['fixture_policy'],p['original_launcher_sha256']) ==
          ('reopen','covers','warm-cache-v2','sealed-private-cache-v2',ORIGINAL_LAUNCHER), 'unexpected_reopen_reference_or_workload')
    check((p['runner_sha256'],p['device_script_sha256'])==(RUNNER, DEVICE),'unqualified_reopen_device_program')
    ids = identities(p,expected_sha)
    for key, filename in (('runner_sha256','runner.py'),('device_script_sha256','device.sh'),('payload_sha256','payload.tar.gz')):
        with (root/filename).open('rb') as f:
            h=sha256()
            for block in iter(lambda: f.read(1024*1024), b''):
                h.update(block)
        check(h.hexdigest() == p[key], 'frozen_input_mismatch')
    flags = {k:r.get(k) is True for k in REF.FLAGS}
    try:
        before, after = ((root / name).read_bytes() for name in ('before.stdout','after.stdout'))
        snapshot = bool(before) and before == after
    except OSError:
        snapshot = False
    try:
        proof=REF.read_json(root/'lease-released.json')
        release=proof.get('run_id')==run and all(proof.get(k) is True for k in ('ok','owner_and_token_checked','native_restored_and_connections_closed','lease_directory_absent_after_release'))
    except Invalid:
        release=False
    return p,r,ids,flags,snapshot,release


def role_clock(raw):
    check(re.fullmatch(r'\d{1,14}\.\d{1,6}',raw),'invalid_sample_clock')
    return int(Decimal(raw)*1_000_000)


def owner_id(directory):
    parts=textfile(directory/'gui-owner').split()
    check(len(parts)==3, 'missing_gui_identity')
    pid,birth,sid=(unsigned(p,2**53-1) for p in parts)
    check(pid>1 and birth>0 and sid>0,'unverified_gui_identity')
    return pid,birth,sid


def fixture(directory, side_options):
    value=textfile(directory/'fixture.sha256');check(re.fullmatch('[a-f0-9]{64}\n',value), 'invalid_fixture_digest')
    check(textfile(directory/'startup-options.sha256') == side_options['options_sha256']+'\n'
          and textfile(directory/'startup-options-presence') == ('present\n' if side_options['startup_options'] else 'absent\n'), 'wrong_visit_options')
    return value


def visit_events(directory, clock, side_options):
    events=REF.read_json(directory/'application.json')['events'];check(isinstance(events,list),'missing_visit_trace')
    selections=[e['name'] for e in events if e['name'].startswith('startup_config_')]
    check(len(selections)==len(OPTION_VALUES) and len(set(selections))==len(selections), 'ambiguous_runtime_option_selection')
    observed={}
    for k,values in OPTION_VALUES.items():
        picked=[v for v in values if 'startup_config_'+k+'_'+v in selections]
        check(len(picked)==1,'missing_runtime_option');observed[k]=picked[0]
    check(all(observed[k]==v for k,v in side_options['startup_options'].items()) and observed['resident']==side_options['startup_options'].get('resident','off'),'application_did_not_consume_options')
    seen={}
    for name in ('exit_requested','shutdown_started','shutdown_complete','resident_library_keeper_published',
                 'resident_library_keeper_unavailable','resident_library_keeper_reclaimed','resident_library_handoff_rejected','cover_resident_pixels_hit'):
        t=[e.get('boottime_us') for e in events if e['name']==name];check(all(REF.natural(k) for k in t),'invalid_keeper_event_clock');seen[name]=t
    once=('exit_requested','shutdown_started','shutdown_complete');check(all(len(seen[n])==1 for n in once),'explicit_cooperative_exit_missing')
    check(clock.covers<=seen['exit_requested'][0]<=seen['shutdown_started'][0]<=seen['shutdown_complete'][0] and seen['shutdown_complete'][0]<=clock.home+10000, 'bad_cooperative_exit_order')
    for name in ('resident_library_keeper_published','resident_library_keeper_unavailable'):
        check(all(seen['shutdown_started'][0]<=t<=seen['shutdown_complete'][0] for t in seen[name]), 'keeper_publication_outside_shutdown')
    check(sum(len(seen[k]) for k in ('resident_library_keeper_published','resident_library_keeper_unavailable'))<=1, 'duplicate_keeper_publication')
    check(sum(len(seen[k]) for k in ('resident_library_keeper_reclaimed','resident_library_handoff_rejected'))<=1, 'ambiguous_keeper_reclaim')
    check(all(clock.request<=t<clock.first for t in seen['resident_library_keeper_reclaimed']+seen['resident_library_handoff_rejected']), 'keeper_reclaim_outside_entry')
    hits=seen['cover_resident_pixels_hit']
    check(all(clock.request<=t<=clock.covers for t in hits) and len(hits)<=6, 'unbounded_or_late_resident_hit')
    check(not hits or len(seen['resident_library_keeper_reclaimed'])==1 and seen['resident_library_keeper_reclaimed'][0]<=min(hits), 'unowned_keeper_results')
    return {'selection':observed,'published':len(seen['resident_library_keeper_published']), 'unavailable':len(seen['resident_library_keeper_unavailable']),
            'reclaimed':len(seen['resident_library_keeper_reclaimed']), 'rejected':len(seen['resident_library_handoff_rejected']), 'pixel_hits':len(hits),
            'explicit_exit':True,'publish_after_shutdown_start_ms':REF.ms(seen['resident_library_keeper_published'][0]-clock.shutdown) if seen['resident_library_keeper_published'] else None}


def samples(directory, prime, second, gui_ids):
    src=textfile(directory/'keeper.tsv').splitlines();bounds=textfile(directory/'resource-intervals.tsv').splitlines()
    check(len(src)==len(bounds)==len(SAMPLING),'missing_resource_observations')
    rows={}; timing={}; peers={}; previous=prime.home-10_000
    for point,line,window in zip(SAMPLING,src,bounds):
        fields=line.split();span=window.split()
        check(len(fields)==9 and len(span)==3 and point==fields[0]==span[0] and fields[1] in ('absent','held','gone'), 'unrecognized_resource_receipt')
        pid,birth,sid,rss=(unsigned(x,2**53-1) for x in fields[2:6]);pss=-1 if fields[6]=='-1' else unsigned(fields[6],2**48-1)
        ticks=unsigned(fields[7],2**53-1); stamp,start,stop=map(role_clock,(fields[8],span[1],span[2]))
        check(previous<=start<=stamp<=stop and ((point=='after_second' and start>=second.home-10000) or (point!='after_second' and stop<=second.request)), 'resource_time_outside_native_home')
        previous=stop;held=fields[1]=='held'
        if held:
            # VmRSS and the PSS walk are independent kernel observations.
            # Their accounting does not warrant rejecting a timing sample when
            # PSS briefly exceeds the approximate, separately sampled VmRSS.
            check(pid>1 and birth>0 and sid==pid and 0<rss<=2**31 and pss<=2**31
                  and all((pid,birth)!=g[:2] for g in gui_ids), 'keeper_identity_or_memory_unproven')
        else:
            check((pid,birth,sid,rss,ticks)==(0,0,0,0,0) and pss in (0,-1), 'absent_keeper_has_resources')
        rows[point]={'state':fields[1], 'rss_kib':rss, 'pss_kib':pss if pss>=0 else None,
                     'sampling_window_ms':REF.ms(stop-start)};peers[point]=(pid,birth,sid) if held else None
        timing[point]=[start,stamp,stop,ticks]
    if peers['between'] and peers['pre_second']:
        check(peers['between']==peers['pre_second'] and timing['pre_second'][3]>=timing['between'][3], 'keeper_identity_or_cpu_counter_changed')
    check(not peers['after_second'],'ordinary_second_exit_left_keeper')
    if any(peers.values()):check(textfile(directory/'holder-fds')=='unix-listener-only\n','keeper_holds_unqualified_resources')
    tick_delta=timing['pre_second'][3]-timing['between'][3] if peers['between'] and peers['pre_second'] else None
    return {'points':rows,'same_keeper_in_home':bool(peers['between'] and peers['pre_second']), 'home_cpu_ticks':tick_delta,
            'home_cpu_window_ms': REF.ms(timing['pre_second'][1]-timing['between'][1]) if tick_delta is not None else None,
            'total_observation_ms': round(sum(x['sampling_window_ms'] for x in rows.values()),3),
            'home_gap_observation_ms': round(sum(rows[p]['sampling_window_ms'] for p in SAMPLING[:2]),3)}


def episode(directory, name, scenario, side_options, commands):
    primary=name[-2:].isdigit();side,idx=name.split('-')
    row={'case':name,'status':'ok','issues':[]};stages={}
    if not directory.is_dir():return dict(row,status='missing',issues=['missing_two_visit_case']),None
    cmd=[c for c in commands if isinstance(c,dict) and c.get('name')=='case-'+name]
    if any(c.get('returncode')==124 or c.get('timeout') is True for c in cmd):row['issues'].append('case_command_timed_out')
    if len(cmd)!=1 or not all(c.get('returncode')==0 for c in cmd):row['issues'].append('case_command_missing_or_failed')
    try:
        check(textfile(directory/'case-ok')=='ok\n','two_visit_case_not_completed')
        pair_inode=textfile(directory/'pair-inode').splitlines()
        check(len(pair_inode)==2 and pair_inode[0]==pair_inode[1] and re.fullmatch(r'\d+:[1-9]\d* \d+:[1-9]\d*',pair_inode[0]), 'mismatched_program_or_data_directory')
        seeds=[];gui=[];clocks=[];mechanisms={}
        for leg in ('prime','second'):
            d=directory/leg;clock,data=audit_leg(d,primary or idx=='warmup');clocks.append(clock)
            gui.append(owner_id(d));seeds.append(fixture(d,side_options));mechanisms[leg]=visit_events(d,clock,side_options)
            stages[leg]=data
        check(gui[0][:2]!=gui[1][:2], 'same_gui_not_a_freshly_admitted_visit')
        check(seeds[0]==seeds[1], 'different_fixture_between_opens')
        link_catalog(directory/'prime',directory/'second')
        a,b=clocks
        row['elapsed_ms']=elapsed(a,b)
        resource=samples(directory,a,b,gui)
        row['resources']=resource;row['visits']=stages;row['actions']=mechanisms
        p,s=(mechanisms[x] for x in ('prime','second'))
        check(p['selection']==s['selection'],'options_changed_between_visits')
        check(p['reclaimed']==p['pixel_hits']==0 and s['published']==s['unavailable']==0,'unexpected_previous_or_republished_keeper')
        new=p['published']==1;held=resource['points']['between']['state']=='held';available=resource['points']['pre_second']['state']=='held'
        check(not held or new,'unowned_data_keeper')
        active_action=side=='candidate' and scenario!='ordinary'
        if not active_action:
            check(not held and not available and not new and not any(p[k] or s[k] for k in ('published','unavailable','reclaimed','rejected','pixel_hits')), 'ordinary_or_disabled_open_used_keeper')
            classification='ordinary_exit'
        elif scenario=='immediate':
            check(not s['reclaimed'] or new and held and available and resource['same_keeper_in_home'], 'reclaim_without_owned_keeper_evidence')
            check(not s['pixel_hits'] or s['reclaimed']==1,'pixel_hit_without_reclaim')
            classification=('retained_pixels_used' if s['pixel_hits']>0 else 'accepted_without_pixel_hit' if s['reclaimed'] else 'normal_reopen_fallback')
            # A request without eligible pixels is a legitimate normal reopen;
            # retain it in the denominators and never upgrade it to a hit.
        elif idx=='qualify':
            check(new and held and not available and not s['reclaimed'] and not s['pixel_hits'], 'fallback_mechanism_not_observed')
            if scenario=='ttl':
                t=textfile(directory/'ttl.tsv').split();check(len(t)==2,'missing_ttl_observation')
                begin,end=map(role_clock,t)
                check(a.shutdown-10000<=begin<=end<=b.request and end+10000>=a.shutdown+45_000_000 and end<=a.home+52_000_000,'not_observed_within_ttl_expiry_window')
                row['ttl_wait_ms']=REF.ms(end-begin);classification='expired_then_normally_reopened'
            else:
                check(textfile(directory/'loss-ok')=='owned-keeper-terminated\n','loss_was_not_intentionally_qualified')
                classification='qualified_lost_keeper_fallback'
        else:
            classification='qualification_warmup'
        row['mechanism']=classification
        row['options_verified']=True;row['new_gui_process_for_each_visit']=True;row['native_home_after_both_visits']=True
        row['elapsed_ms'].update(total_resource_observation=resource['total_observation_ms'],
                                 observations_during_home_gap=resource['home_gap_observation_ms'])
        if row['issues']: row['status']='deadline' if 'case_command_timed_out' in row['issues'] else 'failed'
        return row,seeds[0]
    except Invalid as exc:
        row['issues'].append(str(exc))
    except (OSError,ValueError,TypeError,KeyError,UnicodeError):
        row['issues'].append('missing_or_invalid_private_episode')
    row['status']='deadline' if 'case_command_timed_out' in row['issues'] else 'failed'
    return row,None


def batch_read(root, phase, expected_sha):
    plan,report,ids,flags,snapshot,release=audited_manifest(root,phase,expected_sha)
    names=plan['cases'];check(isinstance(names,list) and len(names)>=2 and len(set(names))==len(names)
        and all(isinstance(n,str) and PUBLIC_CASE.fullmatch(n) for n in names),'bad_predeclared_cases')
    check(names[:2]==[s+'-warmup' for s in SIDES], 'missing_predetermined_warmups')
    issues=['batch_guard_'+k for k,v in flags.items() if not v]
    if not snapshot:issues.append('different_protected_states')
    if not release:issues.append('absent_release_proof')
    cmds=report.get('commands',[]);check(isinstance(cmds,list),'missing_command_log')
    outs=root/'outputs/out';all_rows=[];seeds=set();raw_arms={}
    if outs.is_dir() and any(p.is_dir() and p.name not in names for p in outs.iterdir()):issues.append('unplanned_case_directories')
    for name in names:
        side,idx=name.split('-');row,seed=episode(outs/name,name,plan['reopen_scenario'],ids[side],cmds)
        all_rows.append(row)
        # Baseline warmup seals the reference cache; its initial seed is not
        # the sealed seed intentionally copied to subsequent paired runs.
        if name!='baseline-warmup' and seed is not None:seeds.add(seed)
        if row['status']=='ok':
            selection=dict(row['actions']['prime']['selection']);selection.pop('resident')
            raw_arms.setdefault(side,set()).add(tuple(sorted(selection.items())))
    if any(r['status']!='ok' for r in all_rows):issues.append('planned_episode_missing_or_failed')
    if len(seeds)!=1:issues.append('different_sealed_fixtures')
    if any(len(s)!=1 for s in raw_arms.values()) or len(raw_arms)==2 and raw_arms['baseline']!=raw_arms['candidate']:issues.append('other_runtime_option_varied')
    rows=[r for r in all_rows if r['case'].split('-')[1].isdigit()];pairs=[]
    if phase=='qualify':
        if names!=['baseline-warmup','candidate-warmup','baseline-qualify','candidate-qualify']:issues.append('wrong_qualification_order')
    elif any(n.endswith('-qualify') for n in names):issues.append('qualification_in_primary')
    if len(rows)%2:issues.append('incomplete_pair')
    for left,right in zip(rows[::2],rows[1::2]):
        sl,nl=left['case'].split('-');sr,nr=right['case'].split('-')
        if sl==sr or nl!=nr:issues.append('unmatched_pair_plan');continue
        n=int(nl);order='AB' if sl=='baseline' else 'BA'
        complete=left['status']==right['status']=='ok';p={'batch':plan['run_id'],'round':n,'order':order,'complete':complete}
        if complete:
            a,b=(left,right) if sl=='baseline' else (right,left)
            p.update(baseline_ms=a['elapsed_ms'],candidate_ms=b['elapsed_ms'],savings_ms={k:REF.delta(v,b['elapsed_ms'][k]) for k,v in a['elapsed_ms'].items()})
        pairs.append(p)
    return {'run_id':plan['run_id'],'mode':phase,'scenario':plan['reopen_scenario'],'artifacts':ids,'frozen_plan_sha256':sha256((root/'plan.json').read_bytes()).hexdigest(),
            'runner_sha256':plan['runner_sha256'],'device_script_sha256':plan['device_script_sha256'],'batch_guards':flags,
            'snapshots_match':snapshot,'release_verified':release,'cases':all_rows,'pairs':pairs,'issues':sorted(set(issues))},seeds


def analyze(paths, qualification, phase, expected_sha):
    check(isinstance(expected_sha,str) and DIGEST.fullmatch(expected_sha),'invalid_expected_candidate')
    inputs=[(phase,p) for p in paths]+[('qualify',p) for p in qualification]
    known=set();batches=[];quals=[];seen_seeds=set();issues=[];errors=[];tools=set();identity=set();snapshots=set()
    for i,(kind,root) in enumerate(inputs,1):
        try:
            b,seeds=batch_read(root,kind,expected_sha);run=b['run_id']
            check(run not in known,'same_batch_repeated');known.add(run)
            (quals if kind=='qualify' else batches).append(b);issues.extend(b['issues']);identity.add(json.dumps(b['artifacts'],sort_keys=True));tools.add((b['runner_sha256'],b['device_script_sha256']))
            # Different leases may independently create a SQLite/derivative seed.
            # Byte equivalence is required within a batch. Split confirmation
            # additionally requires the entire boot and protected state to match.
            if kind!='qualify':snapshots.add((root/'before.stdout').read_bytes());seen_seeds.update(seeds)
        except Invalid as exc: errors.append({'input_ordinal':i,'reason':str(exc)})
        except (OSError,ValueError,KeyError,TypeError,UnicodeError): errors.append({'input_ordinal':i,'reason':'missing_or_invalid_input'})
    if len(identity)>1:issues.append('different_artifacts_across_batches')
    if len(snapshots)>1:issues.append('different_original_state_across_timing_batches')
    if len(seen_seeds)>1:issues.append('different_sealed_fixtures_across_timing_batches')
    if len({(b['runner_sha256'],b['device_script_sha256']) for b in batches})>1:issues.append('different_tools_across_timing_batches')
    pairs=[p for b in batches for p in b['pairs']];complete=[p for p in pairs if p['complete']]
    expected=6 if phase=='screen' else 10 if phase=='confirm' else 0;wins=5 if phase=='screen' else 9 if phase=='confirm' else 0
    if expected and ([p['round'] for p in pairs]!=list(range(1,expected+1)) or len(complete)!=expected or any(p['order']!=('AB' if p['round']%2 else 'BA') for p in pairs)):issues.append('wrong_or_incomplete_prespecified_order')
    if phase=='pilot' and (not pairs or any(p['order']!=('AB' if p['round']%2 else 'BA') for p in pairs)):issues.append('wrong_pilot_order')
    rows=[{'side':r['case'].split('-')[0],**r} for b in batches for r in b['cases'] if r['case'].split('-')[1].isdigit()]
    valid=bool(batches or quals) and not issues and not errors
    sums={}
    for k in dict.fromkeys(k for p in complete for k in p['savings_ms']):
        vs=[p['savings_ms'][k] for p in complete]
        sums[k]={s:sample_stats([p[s+'_ms'][k] for p in complete]) for s in SIDES}
        sums[k].update(paired_savings_ms=sample_stats(vs),improved_pairs=sum(x>0 for x in vs),
                       order_savings_ms={ord:sample_stats([p['savings_ms'][k] for p in complete if p['order']==ord]) for ord in ('AB','BA')})
    main='second_covers';guard='second_usable';latency=None
    if valid and expected:
        latency=sums[main]['improved_pairs']>=wins and sums[main]['paired_savings_ms']['median']>=100 and sums[guard]['paired_savings_ms']['median']>=-50
    if not rows:focus=[{'side':r['case'].split('-')[0],**r} for b in quals for r in b['cases'] if r['case'].endswith('-qualify')]
    else:focus=rows
    observed={s:dict(Counter(r.get('mechanism','unverified') for r in focus if r['side']==s)) for s in SIDES}
    hit=observed['candidate'].get('retained_pixels_used',0)
    mechanisms={'name':'data_only_retained_library_pixels','gui_process_reused':False,'counts':observed,
                'verified':bool(valid and hit and observed['baseline'].get('ordinary_exit',0))}
    if quals:
        qsteps={}
        for b in quals:
            role=b['scenario'];qrows=[r for r in b['cases'] if r['case'].endswith('-qualify')];qnames={r.get('mechanism') for r in qrows};needed={'ordinary':'ordinary_exit','immediate':'retained_pixels_used','loss':'qualified_lost_keeper_fallback','ttl':'expired_then_normally_reopened'}[role]
            qsteps.setdefault(role,[]).append(not b['issues'] and len(qrows)==2 and needed in qnames and 'ordinary_exit' in qnames)
        qstatus={k:all(v) for k,v in qsteps.items()}
    else:qstatus={}
    resources={}
    for side in SIDES:
        rs=[r['resources'] for r in focus if r['side']==side and r.get('resources')]
        points=[r['points']['between'] for r in rs]
        held=[p for p in points if p['state']=='held']
        resources[side]={'episodes_observed':len(rs),'keepers_observed':len(held),
                         'keeper_rss_kib_when_present':sample_stats([p['rss_kib'] for p in held]),
                         'keeper_pss_kib_when_available':sample_stats([p['pss_kib'] for p in held if p['pss_kib'] is not None]),
                         'keeper_pss_unavailable':sum(p['pss_kib'] is None for p in held),
                         'idle_cpu_ticks_on_same_keeper':sample_stats([r['home_cpu_ticks'] for r in rs if r['home_cpu_ticks'] is not None]),
                         'same_keeper_observation_ms':sample_stats([r['home_cpu_window_ms'] for r in rs if r['home_cpu_window_ms'] is not None]),
                         'observer_wall_ms':sample_stats([r['total_observation_ms'] for r in rs])}
    status=Counter(r['status'] for r in rows)
    return {'schema_version':1,'phase':phase,'primary_endpoint':'repeat_open','primary_metric':main,'artifacts':(batches+quals)[0]['artifacts'] if batches or quals else {},
            'reopen_policy':'data-only-45-v1','reference_installed_application_sha256':DEPLOYED,'batches':batches+quals,'input_failures':errors,'issues':sorted(set(issues)),
            'pairs':pairs,'planned_primary_cases':len(rows),'primary_case_counts':{k:status[k] for k in ('ok','missing','failed','deadline')},'planned_pairs':len(pairs),'complete_pairs':len(complete),
            'required_pairs':expected or None,'pair_orders':dict(Counter(p['order'] for p in pairs)),'comparisons_ms':sums,'valid':valid,'latency_gate_passed':latency,
            'mechanism':mechanisms,'residency_resources':resources,'qualification_by_scenario':qstatus,'qualification_passed':bool(qstatus and valid and all(qstatus.values())),
            'all_fallbacks_qualified':bool(valid and all(qstatus.get(k) for k in ('ttl','loss','ordinary'))),
            'gate':{'minimum_primary_paired_median_ms':100,'minimum_faster_pairs':wins,'secondary_key':guard,'maximum_secondary_paired_median_regression_ms':50} if expected else None,
            'analyzer_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'reference_analyzer_sha256':WARM,
            'scope':'Two separately authorized GUI processes use the actual Library file URI. A one-shot data helper can retain decoded cover pixels. Timing is software/driver acceptance, not optical panel completion. Both ordinary exit costs, in-app dwell, observed Home gap, resource-sampling windows, actual total wall time and non-hit trials remain visible. CPU ticks describe the observed Home interval only; an RSS snapshot is not peak memory or battery power. Pressure eviction is not certified by these time trials; screenshots and physical power controls require independent qualification.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',type=Path,action='append',required=True);parser.add_argument('--qualification-batch',type=Path,action='append',default=[]);parser.add_argument('--phase',choices=PHASES,required=True);parser.add_argument('--candidate-sha256',help='Expected candidate program; otherwise use the frozen candidate in the first input');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if sys.platform!='linux':parser.error('Run on test-env')
    expected=args.candidate_sha256
    if expected is None:
        try:expected=REF.read_json(args.batch[0]/'plan.json')['variants']['candidate']['application_sha256']
        except (Invalid,TypeError,KeyError):expected=''
    try:out=analyze(args.batch,args.qualification_batch,args.phase,expected)
    except Invalid as exc:out={'phase':args.phase,'valid':False,'issues':[str(exc)],'latency_gate_passed':None,'qualification_passed':False}
    args.output.parent.mkdir(exist_ok=True,parents=True);args.output.write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:out.get(k) for k in ('phase','valid','complete_pairs','latency_gate_passed','qualification_passed','issues','input_failures')}))
    return 0 if out['valid'] else 2


if __name__=='__main__':sys.exit(main())
