#!/usr/bin/env python3
"""Stage a launcher-only diagnostic and emulator-only verification tools."""

import argparse
import json
from pathlib import Path
import runpy
import stat
import subprocess
import zipfile


WORK = Path(__file__).resolve().parent
SOURCE = 'f3e5070577e74a29552f772491edac660ec62a53'
QUERY = 'aidoku/app/aidoku-native-query'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as out:
        out.write(data)


def prepare(release, guest):
    require(WORK.parent == Path('/root') and WORK.name.startswith('aidoku-generation-setup-20261127.')
            and not WORK.stat().st_mode & 0o077, 'Not a fresh private lab workspace')
    require((release/'source.commit').read_text().strip() == SOURCE, 'Wrong release workspace source')
    dist = release/'source/dist'
    proof = json.loads((dist/'development-verification.json').read_bytes())
    require(proof['source'] == SOURCE and proof['members'] == 44 and proof['zip_integrity'] is True
            and proof['all_members_match_staging'] is True and proof['query_arm_hard_float'] is True
            and proof['changed_members_since_previous_development_zip'] == [QUERY], 'Not the qualified development bundle')
    with zipfile.ZipFile(dist/'aidoku-kindle-development.zip') as archive:
        require(len(archive.infolist()) == 44, 'Wrong development package roster')
        contents = {}
        for member in ('aidoku/launch.sh', 'aidoku/app/aidoku', QUERY):
            info = archive.getinfo(member)
            require(info.external_attr >> 16 == stat.S_IFREG | 0o755 and 0 < info.file_size < 55_000_000,
                    'Unexpected executable package member')
            contents[member] = archive.read(info)
            require(contents[member] == (dist/'kindle'/member).read_bytes(), 'Published member differs from build staging')
    original = contents['aidoku/launch.sh']
    helper = contents[QUERY]
    program = contents['aidoku/app/aidoku']
    require(original == (release/'source/packaging/kindle/launch.sh').read_bytes(), 'Launcher differs from pushed source')
    require(all(data.startswith(b'\x7fELF\x01\x01') and data[18:20] == b'\x28\x00' for data in (program, helper))
            and b'/lib/ld-linux-armhf.so.3\0' in helper, 'Wrong native binary ABI')
    base = guest/'source'
    require(original == (base/'packaging/kindle/launch.sh').read_bytes(), 'Qualification launcher source has changed')
    wrapper = (base/'packaging/tests/emulator-wrapper.sh').read_bytes()
    require(wrapper == (release/'source/packaging/tests/emulator-wrapper.sh').read_bytes(), 'Qualification app wrapper changed')
    tools = runpy.run_path(str(WORK/'generation_probe.py'))
    probe = tools['diagnostic_copy'](original)
    driver_path = base/'scripts/verify-launcher-only-qa.py'
    before = b'            result.checks["only_launcher_trace_opt_in"] = True\n'
    after = before + b'''            import runpy
            parser = runpy.run_path(str(self.repository.parent / "generation_probe.py"))
            worker_log = log_path.read_bytes()
            result.checks["generation_diagnostic"] = parser["summarize"](worker_log, parser["worker_from_log"](worker_log))
            result.checks["first_generation_parent_marks"] = len(parser["PARENT"])
            result.checks["observer_child_marks"] = len(parser["CHILD"])
'''
    driver = tools['change'](driver_path.read_bytes(), before, after)
    compile(driver, str(driver_path), 'exec')
    runner = (guest/'verify-firmware.sh').read_bytes()
    runner = tools['change'](runner, b'pending-notifications.aidoku-launcher-$suffix',
                             b'pending-notifications.aidoku-generation-$suffix')
    shell = {'diagnostic-launch.sh': probe, 'guest-mount.sh': (guest/'guest-mount.sh').read_bytes(),
             'verify-firmware.sh': runner, 'source/packaging/kindle/launch.sh': original,
             'source/packaging/tests/emulator-wrapper.sh': wrapper}
    for leaf, content in shell.items():
        subprocess.run(['sh', '-n'], input=content, check=True)
        write(WORK/leaf, content)
    for leaf, content in (('bin/aidoku', program), ('bin/aidoku-native-query', helper),
                          ('source/scripts/verify-launcher-only-qa.py', driver)):
        write(WORK/leaf, content)
    plan = {'source': SOURCE, 'diagnostic': tools['OPT_IN'].decode('ascii'),
            'scope': 'Trace-only launcher preparation; actual application and native-query helper remain unchanged',
            'native_gui_and_helper_match_formal_development': True,
            'launcher_reverses_byte_for_byte_to_main': True,
            'new_process_for_any_marker': False, 'original_outer_marks': len(tools['OUTER']),
            'first_generation_parent_marks': len(tools['PARENT']), 'observer_child_marks': len(tools['CHILD']),
            'firmware_profiles': ['scribe', 'pw6'], 'physical_access': False,
            'physical_artwork_guard_staged': False, 'physical_runner_or_dispatch_payload_staged': False,
            'physical_operator_qualification_pending': True}
    write(WORK/'plan.json', (json.dumps(plan, indent=2)+'\n').encode('ascii'))
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path)
    parser.add_argument('guest_reference', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.release.resolve(strict=True), args.guest_reference.resolve(strict=True)), indent=2))
