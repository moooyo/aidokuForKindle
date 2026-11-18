"""Compare settled desktop L01 pixels with the qualified installed-source captures."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops


ROOT = Path('/root/aidoku-next-status-20261118')
FROZEN = Path('/root/aidoku-startup-cover-final-20261117/public/paired-profiles-r2')
PROFILES = {
    'pw5': ((618, 824), 28, '3cc458b29667ec29e6c48c8e89cb40b36a690f21734a2027fa6d5fe3caa46440'),
    'scribe': ((930, 1240), 36, '9438dfb83f50d0723bbe03cd8761cee78aa2aa1a7d7047cd979591d585f74793'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-reference', action='store_true')
    args = parser.parse_args()
    exe = (FROZEN.parents[1] / 'artifacts/candidate-desktop' if args.check_reference
           else ROOT / 'artifacts/candidate-desktop')
    work = Path(tempfile.mkdtemp(prefix='desktop-pixels-', dir=ROOT / 'reports'))
    env = dict(os.environ, SLINT_BACKEND='winit-software', WINIT_X11_SCALE_FACTOR='1',
               SLINT_SCALE_FACTOR='1', CARGO_BUILD_JOBS='6')
    results = []
    for profile, (size, status_bar, digest) in PROFILES.items():
        frozen = FROZEN / f'candidate-{profile}.png'
        if sha(frozen) != digest:
            raise ValueError(f'The {profile} reference changed')
        png = work / f'candidate-{profile}.png'
        result = subprocess.run(['xvfb-run', '-a', '-s', '-screen 0 1600x1800x24', str(exe),
                                 '--device', profile, '--demo', '--offline', '--screen', 'library',
                                 '--data-dir', str(work / profile), '--capture', str(png),
                                 '--capture-settle-covers', '--performance-trace', str(work / (profile + '.trace.json')),
                                 '--exit-after-ms', '1200'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, timeout=40)
        (work / f'{profile}.stderr').write_bytes(result.stderr)
        result.check_returncode()
        state = json.loads(png.with_suffix('.state.json').read_text())
        metadata = json.loads(png.with_suffix('.metadata.json').read_text())
        if (state['route'] != 'library' or state['cover_visible_failure_count'] or
                state['capture_projection_pending'] or metadata['font_initializations'] != 1 or
                tuple(metadata['window_size']) != size or metadata['scale_factor'] != 1):
            raise ValueError(f'Unsettled or incompatible {profile} capture')
        with Image.open(frozen) as old, Image.open(png) as new:
            if old.size != new.size or new.size != size:
                raise ValueError(f'Changed {profile} geometry')
            diff = ImageChops.difference(old.convert('RGB'), new.convert('RGB'))
            lower = diff.crop((0, status_bar, *size)).getbbox()
            results.append({'profile': profile, 'scene': 'L01', 'size': size,
                            'baseline_png_sha256': digest, 'candidate_png_sha256': sha(png),
                            'full_image_identical': diff.getbbox() is None,
                            'identical_below_status_bar': lower is None})
            if lower is not None:
                raise ValueError(f'Changed {profile} Library pixels')
    result = {'scope': 'Desktop demo L01 product pixels; the physical Kindle status observation is not exercised here.',
              'reference_only': args.check_reference,
              'desktop_elf_sha256': sha(exe), 'comparisons': results, 'passed': True}
    report = work / 'pixel-report.json'
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(report)


if __name__ == '__main__':
    main()
