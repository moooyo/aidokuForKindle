"""Synthetic production-default lifecycle, including raw physical capture replay."""

import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE))
import defaults as d
import release_common as c
import capture_private as cap


class ActualProtocolFixture(unittest.TestCase):
    def setUp(self):
        scratch=tempfile.TemporaryDirectory();self.addCleanup(scratch.cleanup);self.root=Path(scratch.name)
        new=patch.object(c,'ROOT',self.root/'release');new.start();self.addCleanup(new.stop)
        by_name={c.APP:b'\x7fELF\x01\x01synthetic app',c.EXTRA:b'\x7fELF\x01\x01synthetic helper',c.LAUNCH:b'#!/bin/sh\nexit 0\n'}
        program={n:c.sha(data) for n,data in by_name.items()};policy={k:'default_off_optional' for k in c.FEATURES}
        policy.update(layout='default_on',**{'import':'default_on'});self.policy=policy
        authority={'baseline_sha256':c.BASE,'program_sha256':program,'features':policy,'policy_sha256':c.validate_policy(policy),'source_manifest_sha256':'5'*64}
        ap=self.root/'authority.json';ap.write_bytes(c.canonical(authority))
        pathmap={}
        for n,b in by_name.items():
            pathmap[n]=self.root/n;pathmap[n].parent.mkdir(exist_ok=True,parents=True);pathmap[n].write_bytes(b)
        plan=d.prepare(SimpleNamespace(application=pathmap[c.APP],helper=pathmap[c.EXTRA],launcher=pathmap[c.LAUNCH],authority=ap,authority_sha256=c.sha(ap.read_bytes())))
        self.plan=plan;self.batch=plan.parent;self.record=json.loads(plan.read_bytes());self.sha=c.sha(plan.read_bytes());self.run=self.record['run_id']
        self.assertFalse(any((self.batch/name).exists() for name in ('lease-token','transport.json','lease-acquired.json')))
        self.assertNotIn(c.PREVIOUS.encode(),(self.batch/'runner.py').read_bytes());self.assertNotIn(c.PREVIOUS.encode(),(self.batch/'device.sh').read_bytes())
        self.write_common_observations()
        prior=c.module('historical_test_fixture',c.exact(c.PAST/'test_lifecycle.py','e0b0a0aeb4ef4cb195c88b38f40f63ac7fac9d00fe3107d309264a17e847c61c'),c.PAST/'test_lifecycle.py')
        raw=bytearray(cap.HEADER+b'\xff'*(cap.WIDTH*cap.HEIGHT*3));raw[len(cap.HEADER)+(cap.WIDTH//2)*3]=0
        image=gzip.compress(raw,compresslevel=1);body,right=cap.physical(raw)
        self.private_images={}
        for side in ('baseline','candidate'):
            folder=self.batch/'outputs/out'/(side+'-default');folder.mkdir(parents=True)
            fixtures=SimpleNamespace()
            fixtures.directory=folder;fixtures.run=self.run;fixtures.side=side
            fixtures.expected=(program if side=='candidate' else {c.APP:c.BASE,c.LAUNCH:c.LAUNCH_SHA})|{c.JPEG:c.JPEG_SHA,c.QUERY:c.QUERY_SHA}
            prior.CaseEvidenceTests.make_case(fixtures)
            for name in ('cold-fixture','fixture-regime.txt'):(folder/name).write_text(d.REGIME+'\n')
            for name in ('library','wake'):
                (folder/(name+'.sha')).write_bytes(body);(folder/(name+'.right.tsv')).write_bytes(right)
                stem=folder.name+'-'+name
                self.private_images.update({stem+'.native.ppm.gz':image,stem+'.sha':body,stem+'.right.tsv':right})
            for phase in ('before','after'):(folder/('production-options-'+phase)).write_bytes(b'absent\n')
            (folder/'after-ordinary-exit-image').write_bytes(b'absent\n')
            if side=='candidate':
                with (folder/'launcher.log').open('ab') as f:f.write(b'aidoku: Startup portrait supervisor: shell.\n')
                (folder/'production-defaults.txt').write_text(''.join(f'{key}={value}\n' for key,value in d.expected_options(policy).items()))
                (folder/'catalog-before.access').write_text('4100\n');(folder/'catalog-after.access').write_text('4102\n')
        self.seal_outputs();self.seal_images()

    def write_common_observations(self):
        p=self.record; b=self.batch;gates=('success','lease_released','same_boot','native_restored','entry_restored','installed_and_user_unchanged','input_roster_unchanged','temporary_stage_absent','cases_completed')
        names=['before','create-stage','upload','prepare-device','original-program-backup','activate-bridge','case-baseline-default','case-candidate-default',
               'owned-stage-status','repair-launcher','collect','collect-native-private','restore-light','after','remove-owned-stage']
        rep=p|dict.fromkeys(gates,True)|{'completed':d.CASES,'case_exception':None,'commands':[{'name':val,'returncode':0} for val in names]}
        (b/'report.json').write_bytes(c.canonical(rep));(b/'original-program-backup.stdout').write_bytes((b/'original-launcher').read_bytes())
        for case in d.CASES:
            (b/('case-'+case+'.stdout')).write_bytes(b'LIFECYCLE_OK\n');(b/('case-'+case+'.stderr')).write_bytes(b'')
        (b/'owned-stage-status.stdout').write_bytes(b'owned\n');(b/'owned-stage-status.stderr').write_bytes(b'');(b/'after.stderr').write_bytes(b'')
        (b/'collect-native-private.stderr').write_bytes(b'')
        for group in ('repair-launcher','restore-light','remove-owned-stage'):
            for ext in ('.stdout','.stderr'):(b/(group+ext)).write_bytes(b'')
        protection={x:'7'*64 for x in ('/mnt/us/aidoku/data/library.sqlite3','/mnt/us/aidoku/data/device-profile',c.REG.rsplit('/',1)[0]+'/protocol')}
        protection.update({'/mnt/us/aidoku/launch.sh':c.LAUNCH_SHA,'/mnt/us/documents/Aidoku.sh':'709cefdf00318d40a3ee7cb8a5cc8b47e4b8c7865677040779a61264ae086960'})
        snap=''.join(v+'  '+k+'\n' for k,v in protection.items())+'10000000-0000-0000-0000-000000000001\nevent3 13:78\nreader-home\nactive\n15\n9\n'
        for phase in ('before','after'):(b/(phase+'.stdout')).write_text(snap)
        proof={'ok':True,'run_id':self.run,'owner_thread':self.run,'lease_token_sha256':'2'*64}
        (b/'lease-acquired.json').write_bytes(c.canonical(proof|{'acquired_at_utc':'2026-11-18T12:00:00Z'}))
        (b/'lease-released.json').write_bytes(c.canonical(proof|{'released_at_utc':'2026-11-18T12:02:00Z',
                    'owner_and_token_checked':True,'native_restored_and_connections_closed':True,'lease_directory_absent_after_release':True}))

    def seal_outputs(self):
        buf=io.BytesIO();directory=self.batch/'outputs'
        with tarfile.open(fileobj=buf,mode='w:gz') as archive:
            for file in sorted(directory.rglob('*')):
                if not file.is_file():continue
                blob=file.read_bytes();entry=tarfile.TarInfo(file.relative_to(directory).as_posix());entry.size=len(blob);archive.addfile(entry,io.BytesIO(blob))
        for name in ('outputs.tar.gz','collect.stdout'):(self.batch/name).write_bytes(buf.getvalue())

    def seal_images(self):
        buf=io.BytesIO()
        with tarfile.open(fileobj=buf,mode='w:') as archive:
            for n,blob in self.private_images.items():
                member=tarfile.TarInfo(n);member.size=len(blob);archive.addfile(member,io.BytesIO(blob))
        for name in ('native-private.tar','collect-native-private.stdout'):(self.batch/name).write_bytes(buf.getvalue())

    def test_two_complete_physical_cases_replay_the_six_actual_defaults(self):
        verified=d.verify(self.batch,self.sha);self.assertTrue(all(verified['checks'].values()))
        self.assertEqual(verified['baseline_sha256'],c.BASE);self.assertEqual(verified['physical_native_captures'],4)
        self.assertEqual(verified['resolved_options'],d.expected_options(self.policy))
    def test_resealed_overrides_marker_or_foreign_program_cannot_pass(self):
        folder=self.batch/'outputs/out/candidate-default'
        for name,change in [('production-options-before',lambda _:b'present\n'),('production-defaults.txt',lambda b:b.replace(b'resident=off',b'resident=45')),
                            ('launcher.log',lambda b:b.replace(b'supervisor: shell',b'supervisor: native')),('app.argv.nul',lambda b:b+b'--offline\0'),
                            ('ordinary-exit.tsv',lambda _:b'900\t70\n'),('after-ordinary-exit-image',lambda _:b'present\n')]:
            file=folder/name; original=file.read_bytes();file.write_bytes(change(original)); self.seal_outputs()
            with self.subTest(name),self.assertRaises(ValueError):d.verify(self.batch,self.sha)
            file.write_bytes(original)
    def test_native_image_bytes_are_required_even_when_flags_and_hashed_sidecars_look_valid(self):
        self.private_images['candidate-default-wake.native.ppm.gz']=gzip.compress(b'no image');self.seal_images()
        with self.assertRaises(ValueError):d.verify(self.batch,self.sha)


if __name__=='__main__':unittest.main()
