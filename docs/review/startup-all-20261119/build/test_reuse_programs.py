"""Exercise the strict source/permission boundaries on test-env, without publishing a build."""

import copy
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import unittest
import uuid


ROOT = Path('/root/aidoku-all-build-20261119')
M = runpy.run_path(str(ROOT/'tools/reuse_programs.py'))
SOURCE = M['LAUNCH']


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.a = {SOURCE:('file',0o555,8,'old'), 'src/code.rs':('file',0o444,7,'stable'),
                  'src':('directory',0o555)}
        self.b = {**self.a, SOURCE:('file',0o555,10,'new')}

    def test_only_script_content_changes(self):
        M['compare_trees'](self.a, self.b)

    def test_noop_is_not_a_new_implementation(self):
        with self.assertRaises(ValueError):
            M['compare_trees'](self.a,self.a)

    def test_drift_in_another_input_is_rejected(self):
        self.b['src/code.rs']=('file',0o444,7,'changed')
        with self.assertRaises(ValueError):
            M['compare_trees'](self.a,self.b)

    def test_modes_must_match_including_launcher_and_directories(self):
        for path in self.a:
            changed=dict(self.b);v=list(changed[path]);v[1]=0o777;changed[path]=tuple(v)
            with self.subTest(path=path),self.assertRaises(ValueError):
                M['compare_trees'](self.a,changed)

    def test_reject_added_or_deleted_paths(self):
        for changed in ({**self.b,'another':('file',0o444,2,'other')},{k:v for k,v in self.b.items() if k!='src/code.rs'}):
            with self.assertRaises(ValueError):
                M['compare_trees'](self.a,changed)

    def test_real_compiler_and_pristine_source_through_check_only(self):
        # A harmless generated comment exercises the genuine R2 proofs. It is not
        # the proposed production optimization and never receives executable copies.
        label='reuse-fixture-'+uuid.uuid4().hex[:8]; tarpath=ROOT/'incoming'/(label+'.tar')
        with tarfile.open(ROOT/'incoming/final-v2.tar') as old,tarfile.open(tarpath,'x') as out:
            for entry in old:
                item=copy.copy(entry)
                if item.isdir():out.addfile(item);continue
                with old.extractfile(entry) as inp:
                    if item.name!=SOURCE:out.addfile(item,inp);continue
                    data=inp.read()+b'\n# Source-identity test fixture only.\n';item.size=len(data);out.addfile(item,io.BytesIO(data))
        def run(*args):
            return subprocess.check_output([sys.executable,'-B','-I',*map(str,args)],text=True)
        run(ROOT/'tools/integration.py','freeze',label,tarpath)
        obs=json.loads(run(ROOT/'tools/reuse_programs.py',label,'--from','final-v2','--check-only'))
        self.assertEqual(obs['changed_paths'],[SOURCE]);self.assertEqual(obs['new_compilations'],0)
        self.assertEqual(obs['canonical_production_input_count'],369);self.assertEqual(obs['all_files'],483)
        self.assertTrue(obs['original_fresh_cargo_event_verified']);self.assertTrue(obs['archive_modes_match'])
        self.assertFalse(any((ROOT/'artifacts'/label).iterdir()));self.assertFalse((ROOT/'reports'/label/'build-receipt.json').exists())


if __name__ == '__main__':
    unittest.main()
