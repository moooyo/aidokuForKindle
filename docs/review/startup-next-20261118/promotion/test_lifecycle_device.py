"""Exercise the uninstrumented lifecycle shell against an isolated Linux fixture."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('smoke_fixtures', HERE/'test_smoke.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


LIFECYCLE_STUB = '''
import json
stage=root/'media/.aidoku-next-example'; side=(stage/'case').read_text().strip().split('-')[0]
home=stage/side; app=home/'app/aidoku'; log=home/'data/logs/launcher.log'
def observe(parked):
    n='1'*32
    data={'pid':900,'nonce':n,'epoch':1,'phase':'suspended' if parked else 'active','same_process_resumes':int(not parked),
          'platform':{'input_released':parked,'render_paused':parked,'parked':parked,'pause_count':1,'resume_count':int(not parked),
                      'frame_send_count':3+int(not parked),'resume_full_frame_count':int(not parked),'input_generation':1+int(not parked)},
          'application':{'pid':888,'private_fixture_value':'not-for-export','foo':'not-exported'}}
    (lock/'lifecycle-state.json').write_text(json.dumps(data,indent=2))
    write(lock/'epoch',1); write(lock/('sleep-ready' if parked else 'resume-ready'),f'900 {n} 1')
def args_of(proc_id, who):
    args=([str(app),'--kindle','--device','scribe','--data-dir',str(home/'data')]
          if who=='app' else ['sh',str(home/'launch.sh'),'--foreground'])
    if mode=='bad-arg' and who=='app': args+=['--startup-cached-covers','2']
    env=['AIDOKU_HOME='+str(home),'PRIVATE_TEST_VALUE=nevershare']
    if mode=='bad-env' and who=='worker':env+=['AIDOKU_STARTUP_PORTRAIT=native']
    d=proc/str(proc_id); (d/'cmdline').write_bytes(b''.join(x.encode()+bytes([0]) for x in args))
    (d/'environ').write_bytes(b''.join(x.encode()+bytes([0]) for x in env))
'''
PROGRAM = base.MOCK.replace("if name=='lipc-get-prop':", LIFECYCLE_STUB + '''
if name=='lipc-get-prop' and (root/'exit-pending').is_file():
    (root/'exit-pending').unlink();
    for leaf in lock.iterdir(): leaf.unlink()
    lock.rmdir()
if name=='pidof' and (root/'exit-pending').is_file():
    (root/'exit-pending').unlink()
    for leaf in lock.iterdir(): leaf.unlink()
    lock.rmdir()
if name=='lipc-get-prop':''').replace(
    "if not e.is_symlink(): e.symlink_to(app)", """if e.is_symlink():e.unlink()
        e.symlink_to(app); args_of(900,'app');args_of(800,'worker')
        d=proc/'900/fd'; d.mkdir(exist_ok=True)
        if not (d/'2').is_symlink():(d/'2').symlink_to('/dev/input/event5')"""
).replace("elif action=='powerButton': write(lock/'phase','parked')",
          "elif action=='powerButton': write(lock/'phase','parked');observe(True)").replace(
    "write(lock/'phase','active')\n        if mode=='reused-pid'", "write(lock/'phase','active');observe(False)\n        if mode=='forged-ack':write(lock/'resume-ready','900 foreign 1')\n        if mode=='reused-pid'"
).replace("    for p in lock.iterdir(): p.unlink()\n    lock.rmdir()", """    if mode!='missing-exit':write(lock/'ordinary-exit','900 '+('1'*32)+' 1')
    # Native settling finishes a little later than the app's exit publication.
    (root/'exit-pending').touch()""")


class DefaultDevice(unittest.TestCase):
    def setUp(self):
        self.seed=base.InstalledSmoke('test_short_pixel_buffer_rejects'); self.seed.setUp()
        self.addCleanup(self.seed.doCleanups)
        self.stage=self.seed.stage; self.root=self.seed.root; self.env=dict(self.seed.env)
        (self.root/'bin/stub').write_text(PROGRAM)
        # A local lightweight mock models cleanup a poll after ordinary-exit is observed.
        (self.root/'bin/usleep').unlink()
        (self.root/'bin/usleep').write_text('''#!/bin/sh
if test -f "$SMOKE_FIXTURE/exit-pending"; then
    for record in "$SMOKE_FIXTURE"/lock/*; do rm -f "$record"; done
    rmdir "$SMOKE_FIXTURE/lock" && rm "$SMOKE_FIXTURE/exit-pending"
fi
'''); (self.root/'bin/usleep').chmod(0o755)
        self.run_id='aidoku-next-example'; (self.stage/'owner').write_text(self.run_id+'\n')
        (self.stage/'original-catalog.identity').write_text('fixture-identity|text/x-shellscript|thumb|123|500\n')
        (self.stage/'out').mkdir()
        smoke=(self.stage/'smoke.sh').read_text().split('# Entry point.')[0]
        (self.stage/'smoke-lib.sh').write_text(smoke)
        code=(HERE/'lifecycle_device.sh').read_text()
        for old,new in {
                '/mnt/us':str(self.seed.media), '/proc/':str(self.root/'proc')+'/',
                '/var/tmp/aidoku-next-smoke-':str(self.seed.temp/'smoke-'), 'tries=180':'tries=3',
                'tries=500':'tries=3', 'm=180':'m=3', 'm=80':'m=3',
        }.items():
            self.assertIn(old,code); code=code.replace(old,new)
        (self.stage/'lifecycle_device.sh').write_text(code)
        (self.root/'proc/uptime').write_text('123.45 88.01\n')
        for v in ['baseline','candidate']:
            (self.stage/v/'app').mkdir(parents=True); (self.stage/v/'data/logs').mkdir(parents=True)
            (self.stage/'out'/f'{v}-default').mkdir()
            for f in ['app/aidoku','app/aidoku-native-query','app/kindle-jpeg-worker','launch.sh']:
                p=self.stage/v/f; p.write_text('fixture '+v+' '+f); p.chmod(0o755)
        bench=HERE.parent/'tools/bench_device.sh'
        if not bench.is_file(): bench=Path('/root/aidoku-next-bench-20261118/tools/bench_device.sh')
        bench=bench.read_text().replace('/mnt/us',str(self.seed.media)).replace('/var/local',str(self.root/'varlocal'))
        self.runtime=self.root/'varlocal/kindle-catalog-entry/runtime'; self.runtime.mkdir(parents=True)
        self.reg=self.runtime/'aidoku-library-dispatch'; self.reg.write_text('fixture-reg')
        self.original=self.seed.media/'aidoku/launch.sh'; self.original.write_text('fixture-bridge')
        doc=self.seed.media/'documents/Aidoku.sh'; doc.parent.mkdir(); doc.write_text('fixture-document')
        sha=lambda b:hashlib.sha256(b).hexdigest()
        bench=bench.replace('v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86','runtime')
        bench=bench.replace('709cefdf00318d40a3ee7cb8a5cc8b47e4b8c7865677040779a61264ae086960',sha(doc.read_bytes())).replace('2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86',sha(self.reg.read_bytes()))
        (self.stage/'bench-lib.sh').write_text(bench)
        stub=(self.root/'bin/stub').read_text().replace("if 'p_lastAccess' in sql:","if 'SELECT value FROM properties' in sql: print(root/'varlocal/kindle-catalog-entry/runtime/aidoku-library-dispatch')\n    elif 'p_lastAccess' in sql:")
        (self.root/'bin/stub').write_text(stub)
        (self.stage/'inputs.sha256').write_text(sha((self.stage/'capture').read_bytes())+'  capture\n')
        self.env.update(RUN=self.run_id,STAGE=str(self.stage),OWNER_HASH=sha((self.stage/'owner').read_bytes()),BRIDGE_SHA=sha(self.original.read_bytes()),ORIGINAL_SHA='unused')

    def invoke(self, side, ok, case=None):
        if side!='recover': (self.stage/'case').write_text((case or side)+'-default\n')
        result=subprocess.run(['sh',str(self.stage/'lifecycle_device.sh'),side,self.run_id],cwd=self.stage,env=self.env,capture_output=True,text=True,timeout=14)
        self.assertEqual(result.returncode==0,ok,(side,result.stdout,result.stderr))
        return result

    def test_baseline_and_candidate_default_lifecycle(self):
        for side in ('baseline','candidate'):
            self.invoke(side,True)
            out=self.stage/'out'/(side+'-default')
            self.assertEqual((out/'ordinary-exit.tsv').read_text(),'900\t1\n')
            self.assertIn('input_released\ttrue\n',(out/'lifecycle-parked.tsv').read_text())
            self.assertIn('input_released\tfalse\n',(out/'lifecycle-wake.tsv').read_text())
            self.assertNotIn('private_fixture_value',(out/'lifecycle-parked.tsv').read_text())
            self.assertEqual((out/'input-parked.txt').read_text(),'event5\n')
            self.assertEqual((out/'proc-active.txt').read_text(),(out/'proc-wake.txt').read_text())
            self.assertEqual((out/'library.sha').read_text(),(out/'wake.sha').read_text())
            self.assertEqual((out/'library.right.tsv').read_text(),(out/'wake.right.tsv').read_text())
            self.assertNotIn('nevershare',(out/'app.env-relevant').read_text())

    def test_foreign_stage_selection_is_rejected(self):
        self.invoke('candidate',False,'baseline'); self.assertEqual((self.root/'actions').read_text(),'')

    def test_unmeasured_binary_or_worker_override_rejects(self):
        self.seed.mode('bad-env'); self.invoke('candidate',False)
        self.assertFalse((self.stage/'out/candidate-default/case-ok').exists()); self.invoke('recover',True)

    def test_forced_args_are_not_production_defaults(self):
        self.seed.mode('bad-arg'); self.invoke('candidate',False); self.invoke('recover',True)

    def test_forged_ack_fails_even_with_active_and_same_pid(self):
        self.invoke('baseline',True);self.seed.mode('forged-ack'); self.invoke('candidate',False); self.invoke('recover',True)

    def test_reused_runner_is_not_signalled(self):
        self.invoke('baseline',True);self.seed.mode('reused-pid'); self.invoke('candidate',False)
        actions=(self.root/'actions').read_text(); self.invoke('recover',False)
        self.assertEqual(actions,(self.root/'actions').read_text())

    def test_exit_zero_alone_is_insufficient(self):
        self.invoke('baseline',True);self.seed.mode('missing-exit'); self.invoke('candidate',False)
        self.assertFalse((self.stage/'out/candidate-default/ordinary-exit.tsv').exists()); self.invoke('recover',True)

    def test_active_registration_cannot_be_substituted(self):
        self.reg.write_text('unknown-registration'); self.invoke('candidate',False)
        self.assertEqual((self.root/'actions').read_text(),'')

    def test_only_right_toolbar_changes_reject_candidate(self):
        self.invoke('baseline',True);self.seed.mode('right-changed');self.invoke('candidate',False)
        self.assertFalse((self.stage/'out/candidate-default/case-ok').exists());self.invoke('recover',True)

    def test_toolbar_blank_at_the_default_entry_rejects_qualification(self):
        self.seed.mode('flat-white');self.invoke('baseline',False);self.invoke('recover',True)


if __name__=='__main__':
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    unittest.main()
