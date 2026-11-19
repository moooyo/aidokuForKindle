"""Bind a synthetic, two-process import comparison to a frozen guarded base."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
import fixture

BASE=Path('/root/aidoku-all-bench-20261119/base-warm')
DEST=BASE.parent/'import_probe'
SHAS={'run_bench.py':'a6270d6a2d2a78ad7dd1dd59819154dee2f091f85b8bb47bdf7a819fe3711ea6',
      'bench_device.sh':'b81b54db195ec3e0de03f0374e16c614fe75e43b424b918c31e4be1648944f4a'}
FIXTURE_SHA='4bce2ff13a0fab6e04bceb73b1071b86681f0d36fb4ee6eb2d58c91c9039a8a0'


def sha(b): return hashlib.sha256(b).hexdigest()


def once(s,old,new):
    if s.count(old)!=1: raise ValueError('Frozen base anchor changed: '+old[:70])
    return s.replace(old,new,1)


def generate(src=BASE):
    got={k:(src/k).read_bytes() for k in SHAS}
    if any(sha(v)!=SHAS[k] for k,v in got.items()):raise ValueError('Unqualified base inputs')
    r,d=[x.decode() for x in got.values()];r=once(r,str(BASE),str(DEST))
    r=once(r,"if mode == 'screen' and (start, count) != (1, 6):","if mode == 'screen' and (start,count) not in {(1,6),(1,3),(4,3)}:")
    r=once(r,'    variants = {}; files = {}; flags = {}', '''    if ns.trial != 'import' or ns.primary != 'covers': raise ValueError('Use the import trial, covers endpoint')
    cases=[one+'-'+phase for one in cases for phase in ('ingest','reopen')]
    variants = {}; files = {}; flags = {}''')
    r=once(r,"    if ns.trial=='combined'",'''    left,right=variants['baseline'],variants['candidate']
    for k in ('application_sha256','native_query_sha256','launcher_sha256','extra_sha256','args','env'):
        if left[k]!=right[k]: raise ValueError('Import experiments must vary only the import option')
    if any((left['args'],left['env'],left['extra_sha256'])) or left['launcher_sha256']!=LAUNCH_SHA:raise ValueError('Use production launcher and unmodified protocol')
    if left['startup_options']!={'import_thumbnail':'off'} or right['startup_options']!={'import_thumbnail':'idle'}:raise ValueError('Expected off versus idle on same ELF')
    if left['application_sha256']==APP_SHA: raise ValueError('The deployed ELF predates import preparation and its endpoints')
    from_dir=Path(__file__).resolve().parent/'fixture_payload'
    names=['fixture.json','archives.sha256']+[f'comics/S{i:02}.cbz' for i in range(20)]
    for filename in names: add(filename,(from_dir/filename).read_bytes())
    if sha(files['fixture.json'][0])!='EXPECTED_FIXTURE_HASH':raise ValueError('Synthetic input changed')
    if ns.trial=='combined' '''.replace('EXPECTED_FIXTURE_HASH',FIXTURE_SHA))
    r=once(r,"'warm-cache-v2','fixture_policy':'sealed-private-cache-v2'","'synthetic-import-reopen-v1','fixture_policy':'fresh-empty-state-same-arm-reopen','fixture_sha256':'"+FIXTURE_SHA+"'")
    r=once(r,"'fixture_case_seed':'independent'","'fixture_case_seed':'independent-per-arm-pair'")
    r=once(r,'AIDOKU_RESEARCH_DATA="$stage/cases/$case_id/data"','AIDOKU_RESEARCH_DATA="$stage/cases/${{case_id%-*}}/data"')
    old='case "$case_id" in *-warmup) AIDOKU_RESEARCH_DWELL=20000;; *-qualify) AIDOKU_RESEARCH_DWELL=12500; AIDOKU_RESEARCH_CAPTURE_ARGS="--capture $AIDOKU_RESEARCH_OUT/final.png --capture-settle-covers";; esac'
    r=once(r,old,'''case "$case_id" in *-ingest) AIDOKU_RESEARCH_DWELL=35000; AIDOKU_RESEARCH_CAPTURE_ARGS="--import $stage/comics";; *-reopen) AIDOKU_RESEARCH_DWELL=10000;; *) exit 64;; esac
case "$case_id" in *-qualify-ingest) AIDOKU_RESEARCH_DWELL=40000;; *-qualify-reopen) AIDOKU_RESEARCH_DWELL=18000;; esac''')
    r=once(r,"remote('case-'+case,'run_case '+shlex.quote(case),timeout=80)","remote('case-'+case,'run_case '+shlex.quote(case),timeout=105)")
    plugin=Path(__file__).with_name('import_device.sh').read_text()
    d=d[:d.index('fixture() {\n')]+"IMPORT_FIXTURE_SHA="+FIXTURE_SHA+'\n'+plugin+'\n'+d[d.index('apply_case_options() {\n'):]
    d=once(d,'    prepare_case_warm "$C"\n    apply_case_options "$STAGE/cases/$C/data" "$variant"','    import_case "$C"; role=${trial#*-}')
    d=once(d,'savedlog=$STAGE/cases/$C/data/logs/launcher.log','savedlog=$data/logs/launcher.log')
    d=once(d,'    verify_case_options "$STAGE/cases/$C/data" "$variant"','    verify_case_options "$data" "$variant"\n    audit_import')
    check='    grep -F \'"name": "startup_visible_covers_settled"\' "$OUT/application.json" >/dev/null'
    d=once(d,check,'    if [ "$phase" = reopen ]; then '+check.strip()+'; fi')
    d=once(d,'    n=420\n','    n=650\n');d=once(d,'is_app "$p" "$variant"; sleep 6','is_app "$p" "$variant"; if [ "$phase" = ingest ]; then sleep 31; else sleep 12; fi')
    return {'run_bench.py':r,'bench_device.sh':d}


def main():
    if sys.platform!='linux': raise SystemExit('Run only on test-env')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,default=BASE);ns=p.parse_args()
    b=fixture.bundle(); out=DEST/'tools'; out.mkdir(parents=True,exist_ok=True)
    if sha(b['fixture.json'])!=FIXTURE_SHA:raise ValueError('Fixture generation changed')
    for n,v in b.items():
        f=out/'fixture_payload'/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(v)
    files=generate(ns.base)
    subprocess.run([sys.executable,'-I','-c','import sys;compile(sys.stdin.read(),"runner.py","exec")'],input=files['run_bench.py'].encode(),check=True)
    subprocess.run(['sh','-n'],input=files['bench_device.sh'].encode(),check=True)
    for n,v in files.items():(out/n).write_text(v);print(sha(v.encode()),n)
    print(FIXTURE_SHA,'fixture.json')


if __name__=='__main__':main()
