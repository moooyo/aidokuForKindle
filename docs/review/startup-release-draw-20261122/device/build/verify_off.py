"""Link the exact working source's draw-feature-off release library; do not build another application."""

import json
from pathlib import Path
import subprocess


def main():
    root = Path('/work')
    artifacts = [json.loads(line) for line in (root / 'reports/off-artifacts.jsonl').read_text().splitlines()]
    libs = [row for row in artifacts if row.get('reason') == 'compiler-artifact' and row['target']['name'] == 'aidoku_kindle']
    assert len(libs) == 1 and 'startup-stage-diagnostics' in libs[0]['features'] and 'startup-draw-diagnostics' not in libs[0]['features']
    app = next(Path(name) for name in libs[0]['filenames'] if name.endswith('.rlib'))
    assert b'aidoku: startup_draw_cpu sequence=' not in app.read_bytes()
    target = 'armv7-unknown-linux-musleabihf'
    linker, = (Path.home() / '.cache/cargo-zigbuild').glob(f'*/wrappers/*/zigcc-{target}-*.sh')
    probe = root / 'probe/off-probe'
    subprocess.run(['rustc', '--edition=2024', '--target', target, '-C', 'opt-level=2', '-C', f'linker={linker}',
                    '--extern', f'aidoku_kindle={app}', '-L', f'dependency={root}/target/{target}/release/deps',
                    '-L', f'dependency={root}/target/release/deps', root / 'off_probe.rs', '-o', probe], check=True)
    for profile, representative in [('scribe', b'large'), ('pw6', b'compact')]:
        result = subprocess.run(['qemu-arm', probe, profile], capture_output=True, check=True, timeout=30)
        lines = result.stderr.splitlines()
        assert not result.stdout and len(lines) == 3 and [line.split()[1] for line in lines] == [
            b'startup_library_submitted', b'startup_stage_diag', b'startup_transition']
        assert b'layout=' + representative in lines[0]
    print(json.dumps({'target': target, 'same_frozen_source': True, 'production_library_only': True,
                      'compiled_features': libs[0]['features'], 'profiles': ['scribe', 'pw6'],
                      'no_draw_prefix_in_library': True, 'exactly_three_event_types_in_both_processes': True}, indent=2))


if __name__ == '__main__':
    main()
