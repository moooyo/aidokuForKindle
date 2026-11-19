"""Launcher-only release identity, source allowlist and separate historical rejects."""

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import r3_lineage as r


class ReusedBinaries(unittest.TestCase):
    def test_complete_frozen_lineage_replays_and_rejects_a_changed_source_snapshot(self):
        directory=r.c.ROOT/'prepared/r3-launcher-only'
        inputs=r.c.unique_json(r.c.exact(directory/'inputs.json','3abd9103a7469bf48e1cfcb380d7a349ee6cb328db87b103fb19321fc940ca90'))
        expected=r.c.unique_json(r.c.exact(directory/'lineage.json','b2697257ed4cc2030589af32168c4443eeaa0bbfc288b1591251f64947378268'))
        self.assertEqual(r.verify_inputs(inputs),expected)
        original_tree=r.tree; observations=0
        def changed_snapshot(path):
            nonlocal observations
            result=original_tree(path)
            if path==r.R2_SOURCE:
                observations+=1
                if observations==2:
                    result[r.SCRIPT][0]='f'*64
            return result
        with patch.object(r,'tree',side_effect=changed_snapshot):
            with self.assertRaisesRegex(ValueError,'Frozen source changed during lineage verification'):
                r.verify_inputs(inputs)
        self.assertEqual(observations,2)

    def test_host_replay_rejects_a_modified_trace_in_an_independent_copy(self):
        report=r.c.unique_json(r.c.exact(r.RECHECK,r.RECHECK_SHA))
        controls=r.stable_host(report,r.REVIEWED_LAUNCHER_SHA)
        with tempfile.TemporaryDirectory() as name:
            copy_root=Path(name)/'recheck'
            shutil.copytree(r.RECHECK.parent,copy_root)
            with patch.object(r,'RECHECK',copy_root/'checked.json'):
                r.checked_replay(controls,r.REVIEWED_LAUNCHER_SHA)
                trace=copy_root/'raw/admission-fixture/raw/baseline-library.strace'
                trace.write_bytes(trace.read_bytes()+b'corrupted trace\n')
                with self.assertRaisesRegex(ValueError,'Input identity differs'):
                    r.checked_replay(controls,r.REVIEWED_LAUNCHER_SHA)

    def test_only_the_launcher_bytes_may_change_anywhere_in_the_frozen_tree(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);old,new=[root/s for s in ('old','new')]
            for d,mode in ((old,b'old'),(new,b'new')):
                path=d/r.SCRIPT;path.parent.mkdir(parents=True);path.write_bytes(mode);path.chmod(0o755)
                (d/'also').write_bytes(b'same')
            a,b=r.tree(old),r.tree(new);sha=r.c.sha
            data=r.compare_trees(a,b,sha(b'old'),sha(b'new'))
            self.assertEqual(data['unchanged_source_files'],1);self.assertEqual(data['changed_source_files'],[r.SCRIPT])
            (new/'empty-generated-fixture').mkdir()
            with self.assertRaises(ValueError):r.compare_trees(a,r.tree(new),sha(b'old'),sha(b'new'))
            (new/'empty-generated-fixture').rmdir()
            (new/'also').write_bytes(b'changed');
            with self.assertRaises(ValueError):r.compare_trees(a,r.tree(new),sha(b'old'),sha(b'new'))
            (new/'also').write_bytes(b'same');(new/r.SCRIPT).chmod(0o700)
            with self.assertRaises(ValueError):r.compare_trees(a,r.tree(new),sha(b'old'),sha(b'new'))
            (new/'private-link').symlink_to('/tmp');
            with self.assertRaises(ValueError):r.tree(new)

    def test_the_actual_reviewed_patch_recreates_the_candidate_and_cannot_target_another_file(self):
        before=r.c.exact(r.R2_SOURCE/r.SCRIPT,r.R2_LAUNCH);after=r.c.exact(r.QA/'pair/candidate.sh',r.REVIEWED_LAUNCHER_SHA)
        diff=r.c.exact(r.QA/'pair/launcher.patch',r.HOST_DEPENDENCIES['pair/launcher.patch'])
        r.apply_exact(before,after,diff)
        for other in (diff.replace(b'a/packaging/kindle/launch.sh',b'a/another'),diff+b'--- a/data/library.sqlite3\n+++ b/data/library.sqlite3\n'):
            with self.assertRaises(ValueError):r.apply_exact(before,after,other)

    def test_host_evidence_preserves_the_two_distinct_failed_warm_comparisons(self):
        rows=r.rejected_history();self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['application_sha256'],rows[1]['application_sha256'])
        self.assertEqual(rows[1]['application_sha256'],r.R2_APP);self.assertEqual(rows[1]['launcher_sha256'],r.R2_LAUNCH)
        self.assertFalse(any(row['warm_eligible'] for row in rows))
        report=json.loads((r.QA/'host-receipt.json').read_bytes());r.stable_host(report,r.REVIEWED_LAUNCHER_SHA)
        for key in ('candidate_launcher_sha256','helper_sha256','application_sha256'):
            bad=copy.deepcopy(report);bad[key]='f'*64
            with self.assertRaises(ValueError):r.stable_host(bad,r.REVIEWED_LAUNCHER_SHA)
        bad=copy.deepcopy(report);bad['stable']['admission']['library']['candidate']['execs']=999
        with self.assertRaises(ValueError):r.stable_host(bad,r.REVIEWED_LAUNCHER_SHA)

    def test_renaming_r2_changing_a_helper_or_reusing_the_old_guard_cannot_admit_r3(self):
        candidate=r.R2_PROGRAM|{r.c.LAUNCH:r.REVIEWED_LAUNCHER_SHA};h='f'*64
        policy={f:'default_on' if f=='layout' else 'default_off_optional' for f in r.c.FEATURES}
        rec={'release_generation':'r3-launcher-only','r3_lineage_sha256':h,'r3_lineage':{'report':{'sha256':h}},
             'baseline_sha256':r.c.BASE,'source_manifest_sha256':r.COMPILED_SOURCE,'program_sha256':candidate,
             'features':policy,'policy_sha256':r.c.validate_policy(policy)}
        proof={'program_sha256':candidate,'generation':rec['release_generation'],'promoted':False,'performance_claim':False}
        with patch.object(r,'verify',return_value=proof):
            self.assertEqual(r.generation(rec)['r3_lineage_sha256'],h)
            for why in ('same','helper','identity','unlabeled','settings'):
                bad=copy.deepcopy(rec)
                if why=='same':bad['program_sha256'][r.c.LAUNCH]=r.R2_LAUNCH
                if why=='helper':bad['program_sha256'][r.c.EXTRA]='b'*64
                if why=='identity':bad['r3_lineage_sha256']='a'*64
                if why=='unlabeled':del bad['release_generation']
                if why=='settings':bad['features']['import']='default_on'
                with self.subTest(why),self.assertRaises(ValueError):r.generation(bad)
        with self.assertRaises(ValueError):r.generation({'program_sha256':r.R2_PROGRAM})
        self.assertIsNone(r.generation({'program_sha256':r.R2_PROGRAM},prior_observation=True))


if __name__=='__main__':unittest.main()
