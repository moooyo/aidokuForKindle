"""Independent Linux fixtures for baseline pinning, per-case state and settings."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest


HERE=Path(__file__).resolve().parent
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

prep=load('all_prep',HERE/'prepare_bench.py')
sha=lambda b:hashlib.sha256(b).hexdigest()


class Conditions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.warm=load('mixed_warm',prep.ROOT/'base-warm/run_bench.py')
        cls.cold=load('mixed_cold',prep.ROOT/'base-cold/run_bench.py')

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.base=Path(t.name)
        self.run_id='aidoku-all-host-abcdef01';self.stage=self.base/('.'+self.run_id);self.stage.mkdir();(self.stage/'owner').write_text(self.run_id+'\n')
        self.app=self.base/'aidoku/data';self.app.mkdir(parents=True);(self.app/'device-profile').write_text('scribe\n')
        with sqlite3.connect(self.app/'library.sqlite3') as db: db.execute('CREATE TABLE fixture (i)');db.execute('INSERT INTO fixture VALUES (4)')
        cache=self.app/'cache/shared-v1';(cache/'raw').mkdir(parents=True);(cache/'processed').mkdir()
        with sqlite3.connect(cache/'index.sqlite3') as db:
            db.execute('CREATE TABLE artifacts (id,kind,cache_key,valid)');db.execute('CREATE TABLE aliases(artifact_id,cover)')

    def sh(self,kind,cmd,check=True):
        dev=prep.generate(kind)['bench_device.sh'].replace('/mnt/us',str(self.base)).replace('/tmp/aidoku-launcher.lock',str(self.base/'lock'))
        prefix=f'RUN={self.run_id}\nSTAGE={self.stage}\nOWNER_HASH={sha((self.run_id+chr(10)).encode())}\n'
        return subprocess.run(['sh','-s'],input=prefix+dev+'\n'+cmd+'\n',text=True,capture_output=True,check=check)

    def test_exact_deployed_reference_and_frozen_lock(self):
        self.assertEqual(prep.APP,sha(Path('/root/aidoku-next-status-20261118/artifacts/candidate-arm').read_bytes()))
        for kind in ('warm','cold'):
            a=prep.generate(kind)
            for n,v in a.items():self.assertEqual(v.encode(),(prep.ROOT/('base-'+kind)/n).read_bytes());self.assertNotIn(prep.OLD_APP,v)
            self.assertIn(prep.APP,a['bench_device.sh']);self.assertIn(self.warm.LAUNCH_SHA,a['run_bench.py']);self.assertIn('file:///mnt/us/documents/Aidoku.sh',a['bench_device.sh']);self.assertNotIn('UPDATE properties',a['bench_device.sh'])

    def test_warm_seed_populated_only_from_baseline_then_immutable(self):
        self.sh('warm','fixture\nOUT="$STAGE/out/baseline-warmup"; mkdir "$OUT"; prepare_case_warm baseline-warmup')
        seedcase=self.stage/'cases/baseline-warmup/data/cache/shared-v1';(seedcase/'processed/new.png').write_bytes(b'warm bytes')
        (self.stage/'out/baseline-warmup/case-ok').write_text('ok\n')
        for case in ('candidate-warmup','baseline-01','candidate-01'):
            self.sh('warm',f'OUT="$STAGE/out/{case}"; mkdir "$OUT"; prepare_case_warm {case}')
            p=self.stage/f'cases/{case}/data/cache/shared-v1/processed/new.png';self.assertEqual(p.read_bytes(),b'warm bytes')
            p.write_bytes(b'changed case only');self.assertEqual((self.stage/'out'/case/'fixture.sha256').read_bytes(),(self.stage/'sealed.sha256').read_bytes())
        self.assertFalse((self.app/'cache/shared-v1/processed/new.png').exists());self.assertNotEqual(self.sh('warm','OUT="$STAGE/out/baseline-01"; prepare_case_warm baseline-01',False).returncode,0)

    def test_each_cold_case_starts_empty(self):
        self.sh('cold','fixture')
        for case in ('baseline-warmup','candidate-01'):
            self.sh('cold',f'OUT="$STAGE/out/{case}"; mkdir "$OUT"; prepare_case_cold {case}')
            data=self.stage/f'cases/{case}/data';self.assertFalse((data/'cache').exists());(data/'cache').mkdir();(data/'cache/new').write_text('private only')
            self.assertEqual((self.stage/'out'/case/'cold-fixture').read_text(),'empty-cache-v1\n')
        self.assertFalse((self.app/'cache/new').exists())

    def test_options_explicit_and_default_absent(self):
        self.sh('cold','fixture')
        for v,raw in [('baseline',b'first_layout=baseline\n'),('candidate',b'')]:
            src=self.stage/v;src.mkdir();(src/'bench.options').write_bytes(raw);(src/'bench.options.sha256').write_text(sha(raw)+'\n')
            case=v+'-01';out=self.stage/'out'/case;data=self.stage/'cases'/case/'data'
            init=f'OUT="$STAGE/out/{case}"; mkdir "$OUT"; prepare_case_cold {case}; apply_case_options "$STAGE/cases/{case}/data" {v}; verify_case_options "$STAGE/cases/{case}/data" {v}'
            self.sh('cold',init);self.assertEqual((out/'startup-options.sha256').read_text(),sha(raw)+'\n');self.assertEqual((data/'startup-options').exists(),bool(raw))
            self.assertEqual((out/'startup-options-presence').read_text(),'present\n' if raw else 'absent\n')
            (data/'startup-options').write_bytes(b'tampered');self.assertNotEqual(self.sh('cold',f'OUT="$STAGE/out/{case}"; verify_case_options "$STAGE/cases/{case}/data" {v}',False).returncode,0)
        self.assertFalse((self.app/'startup-options').exists())

    def test_allowed_options_canonical_content(self):
        values,raw=self.warm.case_options(SimpleNamespace(baseline_option=['resident=off','prewarm=2','first_layout=optimized']),'baseline')
        self.assertEqual(raw,b'first_layout=optimized\nprewarm=2\nresident=off\n');self.assertEqual(values['prewarm'],'2')
        for rows in [['prewarm=4'],['prewarm=0','prewarm=2'],['key=path'],['resident=45\na=1']]:
            with self.assertRaises(ValueError):self.warm.case_options(SimpleNamespace(baseline_option=rows),'baseline')

    def test_bridge_requires_exact_registered_document_handoff(self):
        cases=self.warm.case_order('pilot',1,1);out=self.stage/'out/candidate-01';out.mkdir(parents=True)
        (self.stage/'case').write_text('candidate-01\n'); app=self.stage/'candidate';app.mkdir();(app/'launch.sh').write_text('printf "%s\\n" "$AIDOKU_RESEARCH_DATA"')
        bridge=self.stage/'bridge';bridge.write_bytes(self.warm.bridge(self.run_id,cases,{v:':' for v in ('baseline','candidate')}).replace(b'/mnt/us',str(self.base).encode()))
        env=dict(os.environ);env.update(AIDOKU_CATALOG_NOTIFY_HELPER='/var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86/aidoku-catalog-client',AIDOKU_CATALOG_NOTIFY_SHA256='f46a0225d5a265f5f9749029fc115060576508ee5caadf25becb661738b88161')
        ok=subprocess.run(['sh',bridge,'--library'],env=env,capture_output=True,text=True);self.assertEqual(ok.returncode,0,ok.stderr);self.assertEqual(ok.stdout.strip(),str(self.stage/'cases/candidate-01/data'))
        self.assertNotEqual(subprocess.run(['sh',bridge,'--foreground'],env=env).returncode,0)
        env.pop('AIDOKU_CATALOG_NOTIFY_HELPER');self.assertNotEqual(subprocess.run(['sh',bridge,'--library'],env=env).returncode,0)

    def test_pairing_and_side_effect_identity(self):
        for runner in (self.cold,self.warm):
            self.assertEqual(len(runner.case_order('screen',1,6)),14);self.assertEqual(runner.case_order('confirm',1,10)[2:],runner.case_order('confirm',1,5)[2:]+runner.case_order('confirm',6,5)[2:]);self.assertEqual(len(set(runner.case_order('confirm',1,10))),22)
            self.assertEqual(runner.APP_SHA,prep.APP)


if __name__=='__main__':unittest.main()
