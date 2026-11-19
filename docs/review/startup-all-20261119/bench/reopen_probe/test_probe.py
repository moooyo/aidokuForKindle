"""Linux host tests, including observing the real data-only transport process."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
import unittest

ROOT=Path('/root/aidoku-all-bench-20261119/reopen_probe')
PROBE=Path('/root/aidoku-all-resident-integration-20261119/target/release/aidoku-resident-probe')


class Reopen(unittest.TestCase):
    def test_pinned_actions_and_two_formal_opens(self):
        plans=list((ROOT/'private').glob('aidoku-all-rapr2-*/plan.json'));self.assertGreaterEqual(len(plans),4)
        for path in plans:
            p=json.loads(path.read_text());self.assertEqual(p['reopen_policy'],'data-only-45-v1');self.assertEqual(p['reopen_action_ms'],6000)
            a,b=(p['variants'][s] for s in ('baseline','candidate'))
            self.assertEqual(a['application_sha256'],b['application_sha256']);self.assertEqual(a['launcher_sha256'],b['launcher_sha256']);self.assertEqual(a['extra_sha256'],b['extra_sha256'])
            self.assertEqual(a['startup_options'],{'resident':'off'});self.assertEqual(b['startup_options'],{'resident':'45'})
            with tarfile.open(path.with_name('payload.tar.gz')) as ar:
                g=ar.extractfile('bridge.sh').read().decode();sc=p['reopen_scenario'];self.assertIn(f'[ "{sc}" != ordinary ]',g)
                script=ar.extractfile('candidate/launch.sh').read().decode(); self.assertEqual(script.count('--actions "$AIDOKU_RESEARCH_ACTIONS"'),2)
                for action in ('exit','quick-return'):
                    for ms in (6000,18000): self.assertEqual(json.load(ar.extractfile(f'{action}-{ms}.json')),[{'at_ms':ms,'section':'app','action':action}])
                self.assertIn('AIDOKU_RESEARCH_DATA="$stage/cases/$pair_id/data"',g)
                self.assertIn('baseline-warmup-prime|baseline-warmup-second',g)
            shell=path.with_name('device.sh').read_text();self.assertIn('start file:///mnt/us/documents/Aidoku.sh',shell)
            self.assertIn('single_visit "$pair-prime"',shell);self.assertIn('single_visit "$pair-second"',shell)
            self.assertIn('gui_identity',shell);self.assertIn('stable_entry;native_wait;printf',shell)
            self.assertEqual(hashlib.sha256(shell.encode()).hexdigest(),p['device_script_sha256'])

    def test_exact_native_keeper_and_foreign_inode(self):
        raw=(ROOT/'bench_device.sh').read_text();src=raw[raw.index('reopen_proc() {'):]
        with tempfile.TemporaryDirectory() as name:
            stage=Path(name);slot=stage/'candidate/app';slot.mkdir(parents=True); foreign=stage/'baseline/app';foreign.mkdir(parents=True)
            exe=slot/'aidoku';shutil.copy2(PROBE,exe);shutil.copy2(PROBE,foreign/'aidoku');data=stage/'data';data.mkdir();group=stage/'out';group.mkdir()
            launch=subprocess.run([exe,'publish-and-exit',data],capture_output=True,check=True,text=True);pid,birth=launch.stdout.split();pid=int(pid);pidfd=os.pidfd_open(pid)
            def sample(side, key):
                pre=f'set -eu\nSTAGE={stage}\nGROUP={group}\nvariant={side}\n'
                subprocess.run(['sh','-s'],input=pre+src+'\nrole_sha_init; reopen_resource '+key+'\n',text=True,check=True)
                return (group/'keeper.tsv').read_text().splitlines()[-1].split()
            try:
                a=sample('baseline','between');self.assertEqual(a[1],'absent')
                a=sample('candidate','between');self.assertEqual(a[1:5],['held',str(pid),birth,str(pid)]);self.assertGreater(int(a[5]),0)
                self.assertEqual((group/'holder-fds').read_text(),'unix-listener-only\n')
                b=sample('candidate','pre_second');self.assertEqual(a[2:5],b[2:5]);self.assertEqual(len((group/'resource-intervals.tsv').read_text().splitlines()),3)
            finally:
                # The kernel handle binds this host fixture's exact child, not
                # a process selected by name or an unchecked numeric PID.
                try:signal.pidfd_send_signal(pidfd,signal.SIGTERM)
                except ProcessLookupError:pass
                os.close(pidfd)

    def test_loss_signals_only_the_owned_generation(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name);stage=base/'.aidoku-all-host-1234abcd';slot=stage/'candidate/app';slot.mkdir(parents=True)
            binary=slot/'aidoku';shutil.copy2(PROBE,binary);helper=stage/'drop-owned-keeper';data=stage/'data';data.mkdir()
            prefix=str(base/'.aidoku-all-')
            subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-DREOPEN_EXPECTED_ELF_BYTES=0',f'-DREOPEN_BENCH_STAGE_PREFIX="{prefix}"',str(ROOT/'adapter/drop_owned_keeper.c'),'-o',str(helper)],check=True)
            subprocess.run([helper,'--supported'],check=True);pid,birth=subprocess.check_output([binary,'publish-and-exit',data],text=True).split();fd=os.pidfd_open(int(pid))
            try:
                (stage/'case').write_text('candidate-qualify-prime\n');(stage/'owner').write_text('foreign\n')
                self.assertEqual(subprocess.run([helper,pid,birth]).returncode,65)
                (stage/'owner').write_text('aidoku-all-host-1234abcd\n');(stage/'case').write_text('candidate-01-prime\n')
                self.assertEqual(subprocess.run([helper,pid,birth]).returncode,65)
                (stage/'case').write_text('candidate-qualify-prime\n')
                self.assertEqual(subprocess.run([helper,pid,str(int(birth)+1)]).returncode,65)
                self.assertEqual(subprocess.run([helper,str(os.getpid()),birth]).returncode,65)
                self.assertEqual(subprocess.check_output([helper,pid,birth],text=True),'owned-keeper-terminated\n')
            finally:
                try:signal.pidfd_send_signal(fd,signal.SIGTERM)
                except ProcessLookupError:pass
                os.close(fd)


if __name__=='__main__':unittest.main()
