"""Exercise the complete host freezer and its default-options bridge, without a lease."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location('fixture_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


d = load('default_lifecycle'); samples = load('test_prepare')
SOURCE = Path('/root/aidoku-startup-delivery-20261118/source')


class FreezerChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.new = patch.object(d, 'ROOT', self.root/'lifecycle'); self.new.start(); self.addCleanup(self.new.stop)
        self.candidate = b'trial-application'; self.blobs = {}
        attested = samples.digest(self.candidate)
        screen, _ = samples.trial('screen', 'cold'); confirm, _ = samples.trial('confirm','cold')
        pixels, _, _ = samples.catalog_qualification('cold'); pixels.update(
            mechanism={'name':'cold_local_cover_decode_validation','verified':True},
            cache_regime='empty-cache-v1',fixture_policy='per-case-empty-cache-v1',cold_fixture_verified=True,cold_fixture_match=True)
        for batch in pixels['batches']:
            batch.update(cache_regime='empty-cache-v1',fixture_policy='per-case-empty-cache-v1')
        source_hash = json.loads(Path('/root/aidoku-startup-delivery-20261118/public/source-manifest.json').read_text())['sha256']
        self.screen, self.confirm, self.pixels = screen, confirm, pixels
        self.proof = {'source_manifest_sha256':source_hash,'artifact_sha256':{samples.APP:attested},
                      'evidence':[self.bound('report',b'synthetic build attestation')]}
        self.selection = {'schema':1,'source_manifest_sha256':source_hash,
                          'artifacts':{samples.APP:self.bound('candidate-arm',self.candidate)},'candidates':{'cold':{}},'qualification':{}}

    def bound(self, name, data):
        if not isinstance(data,bytes): data=json.dumps(data).encode('ascii')
        p=self.root/name; p.write_bytes(data); return {'uri':p.as_uri(),'sha256':d.sha(data)}

    def prepare(self):
        self.selection['candidates']['cold']={'screen':self.bound('s',self.screen),'confirm':self.bound('c',self.confirm)}
        self.selection['qualification']=self.bound('q',self.pixels)
        a = self.bound('build-attestation.json',self.proof); s=self.bound('selection.json',self.selection)
        return d.freeze(SimpleNamespace(selection=self.root/'selection.json',selection_sha256=s['sha256'],
            source_root=SOURCE,build_attestation=self.root/'build-attestation.json',build_attestation_sha256=a['sha256']))

    def test_partitioned_defaults_are_uninstrumented_and_transport_is_offline(self):
        plan=self.prepare(); batch=plan.parent; obj=json.loads(plan.read_text())
        self.assertEqual(obj['cases'],['baseline-default','candidate-default']); self.assertEqual(obj['candidate_names'],['cold'])
        self.assertTrue(all(v['args']==[] and v['env']=={} for v in obj['variants'].values()))
        self.assertFalse(any((batch/name).exists() for name in ('lease-token','transport.json','lease-acquired.json')))
        with tarfile.open(batch/'payload.tar.gz') as payload:
            get = lambda n:payload.extractfile(n).read()
            base=(d.DEPOT/'aidoku/launch.sh').read_bytes()
            self.assertEqual(get('baseline/launch.sh'),base); self.assertEqual(get('candidate/launch.sh'),base)
            self.assertEqual(get('candidate/binary-hash'),(d.sha(self.candidate)+'\n').encode())
            bridge=get('bridge.sh'); self.assertNotIn(b'--startup',bridge); self.assertNotIn(b'AIDOKU_STARTUP',bridge)
            self.assertNotIn(b'--offline',bridge); self.assertIn(b'--library',bridge); self.assertIn(b'AIDOKU_HOME=',bridge)
        self.assertIn('sh "$STAGE/lifecycle_device.sh" recover', (batch/'device.sh').read_text())

    def test_failure_to_meet_timing_gate_cannot_reserve_a_lease(self):
        self.screen['latency_gate_passed']=False
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(d.ROOT.exists())

    def test_cold_mix_and_failed_lifecycle_source_proof_stay_closed(self):
        self.pixels['cache_regime']='warm-cache-v1'
        with self.assertRaises(ValueError): self.prepare()
        self.pixels['cache_regime']='empty-cache-v1'
        self.proof['artifact_sha256'][samples.APP]='a'*64
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(d.ROOT.exists())

    def test_generated_shell_parses_and_has_only_owned_recovery(self):
        for cold in (False,True):
            text=d.device_text(cold); subprocess.run(['sh','-n'],input=text,check=True)
            recovery=text.split(b'restore_launcher() {')[1]
            self.assertIn(b'LIFECYCLE_RECOVERED',recovery); self.assertNotIn(b'kill -CONT',recovery)
            self.assertNotIn(b'kill -TERM',recovery); self.assertNotIn(b'UPDATE properties',text)
        with self.assertRaises(ValueError):d.bridge('unrelated')


if __name__ == '__main__':
    unittest.main()
