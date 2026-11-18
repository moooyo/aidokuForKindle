"""Exercise the isolated cold experiment on host files; never open a device."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest


HERE=Path(__file__).resolve().parent
modspec=importlib.util.spec_from_file_location('cold_tools',HERE/'materialize.py')
cold=importlib.util.module_from_spec(modspec);modspec.loader.exec_module(cold)
sha=lambda b:hashlib.sha256(b).hexdigest()


class ColdFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated=cold.generate()

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.base=Path(t.name)
        self.run_id='aidoku-next-cold-1234abcd';self.stage=self.base/('.'+self.run_id);self.stage.mkdir();(self.stage/'owner').write_text(self.run_id+'\n')
        self.app=self.base/'aidoku/data';self.app.mkdir(parents=True);(self.app/'device-profile').write_text('scribe\n')
        with sqlite3.connect(self.app/'library.sqlite3') as db:db.execute('CREATE TABLE fixture (i INTEGER)');db.execute('INSERT INTO fixture VALUES (4)');db.commit()
        realcache=self.app/'cache/shared-v1';(realcache/'raw').mkdir(parents=True);(realcache/'processed').mkdir();(realcache/'index.sqlite3').write_bytes(b'original cache');(realcache/'raw/source').write_bytes(b'source')

    def shell(self,code,check=True):
        dev=self.generated['bench_device.sh'].replace('/mnt/us',str(self.base)).replace('/tmp/aidoku-launcher.lock',str(self.base/'lock'))
        pre=f'RUN={self.run_id}\nSTAGE={self.stage}\nOWNER_HASH={sha((self.run_id+chr(10)).encode())}\n'
        return subprocess.run(['sh','-s'],input=pre+dev+'\n'+code+'\n',text=True,capture_output=True,check=check)

    def test_seed_and_fresh_per_case(self):
        self.shell('fixture\nOUT="$STAGE/out/baseline-01"; mkdir "$OUT"; prepare_case_cold baseline-01')
        a=self.stage/'cases/baseline-01/data';(a/'cache').mkdir();(a/'cache/warm').write_bytes(b'generated');(a/'library.sqlite3').write_bytes(b'touched research db')
        self.shell('OUT="$STAGE/out/candidate-01"; mkdir "$OUT"; prepare_case_cold candidate-01')
        report=self.stage/'out/candidate-01'; b=self.stage/'cases/candidate-01/data'
        self.assertFalse((b/'cache').exists());self.assertEqual({f.name for f in b.iterdir()},{'device-profile','library.sqlite3'})
        self.assertEqual((self.stage/'fixture/library.sqlite3').read_bytes(),(b/'library.sqlite3').read_bytes())
        self.assertEqual((report/'cold-fixture').read_text(),'empty-cache-v1\n'); self.assertEqual((report/'cold-fixture.sha256').read_text().strip(),sha((report/'seed.sha256').read_bytes()))
        self.assertEqual((self.stage/'out/baseline-01/cold-fixture.sha256').read_bytes(),(report/'cold-fixture.sha256').read_bytes())
        self.assertEqual((self.app/'cache/shared-v1/index.sqlite3').read_bytes(),b'original cache')
        self.assertNotEqual(self.shell('OUT="$STAGE/out/candidate-01"; prepare_case_cold candidate-01',False).returncode,0)
        with sqlite3.connect(self.app/'library.sqlite3') as db:self.assertEqual(db.execute('SELECT i FROM fixture').fetchall(),[(4,)])

    def test_seed_mismatch_and_symlink_refused(self):
        self.shell('fixture');(self.stage/'fixture/device-profile').write_text('different\n')
        self.assertNotEqual(self.shell('OUT="$STAGE/out/baseline-01"; mkdir "$OUT"; prepare_case_cold baseline-01',False).returncode,0)
        (self.stage/'cases/candidate-01').symlink_to(self.app,target_is_directory=True)
        self.assertNotEqual(self.shell('OUT="$STAGE/out/candidate-01"; mkdir "$OUT"; prepare_case_cold candidate-01',False).returncode,0)

    def test_miss_count_rejects_warm_and_partial(self):
        out=self.stage/'out';out.mkdir()
        for misses,hits,jobs,ok in [(6,0,6,True),(5,0,6,False),(6,1,6,False),(6,0,5,False)]:
            names=misses*['cover_local_derivative_prepared']+hits*['cover_local_derivative_reused']+jobs*['cover_preparation_finished']
            (out/'application.json').write_text(json.dumps({'events':[{'name':n} for n in names]},indent=2)); r=self.shell('OUT="$STAGE/out"; check_cold_case',False)
            self.assertEqual(r.returncode==0,ok);self.assertEqual((out/'cold-counts').read_text(),f'{misses} {hits}\n')

    def test_real_cache_snapshot_flags_state_change(self):
        base=self.shell('real_cache_snapshot').stdout
        self.assertEqual(self.shell('real_cache_snapshot').stdout,base)
        p=self.app/'cache/shared-v1/raw/source';p.write_bytes(b'new different size')
        self.assertNotEqual(self.shell('real_cache_snapshot').stdout,base)
        (self.app/'cache/shared-v1/index.sqlite3').write_bytes(b'new index');self.assertNotEqual(self.shell('real_cache_snapshot').stdout,base)

    def test_driver_common_device_bounds(self):
        dev=self.generated['bench_device.sh'];host=self.generated['run_bench.py']
        self.assertIn('AIDOKU_RESEARCH_DATA="$stage/cases/$case_id/data"',host);self.assertIn('AIDOKU_RESEARCH_DWELL=20000;',host)
        self.assertIn("'cache_regime':'empty-cache-v1'",host);self.assertIn("Cold comparison requires the installed baseline",host)
        self.assertLess(dev.index('prepare_case_cold "$C"'),dev.index('mark native_open_requested'));self.assertNotIn('rm -rf',dev[dev.index('fixture() {'):dev.index('prepare_device() {')]);self.assertIn('check_cold_case\n    stable_entry;',dev)
        self.assertIn('start file:///mnt/us/documents/Aidoku.sh',dev);self.assertNotIn('doFullScan',dev)
        for line in dev.splitlines():
            if 'sqlite3 ' in line and ('"$DB"' in line or '"$REG"' in line):self.assertIn('sqlite3 -readonly ',line)


if __name__=='__main__':unittest.main()
