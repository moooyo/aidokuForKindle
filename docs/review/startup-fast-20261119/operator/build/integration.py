"""Run the frozen release builder against independent R3 successor snapshots."""

import hashlib
from pathlib import Path
import sys

ROOT = Path('/root/aidoku-launch-fast-20261119')
ORIGINAL = Path('/root/aidoku-all-build-20261119/tools/integration.py')
ORIGINAL_SHA = '8d6d97ae4faa02f83a7e8462a2ff412b224bc6c2291a8a085b19e506a0e07051'
R3 = Path('/root/aidoku-all-build-20261119/sources/final-v3-clean')
R3_SOURCE = '28c1bb314e12fe5689b401ca23cf932a91c138f5763c4c1ea18d454f41c990cf'


def load():
    raw = ORIGINAL.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA:
        raise ValueError('Historical build engine identity changed')
    engine = {'__name__': 'release_builder', '__file__': str(ORIGINAL)}
    exec(compile(raw, str(ORIGINAL), 'exec'), engine)
    engine.update(ROOT=ROOT, BASE=R3, PREVIOUS=R3_SOURCE,
                  REVISION='8baf57ffb64d74000abef49111baf6e726d4d425',
                  DEPOT_HEAD='3d8f0d7d5d2981d56f9df08c092fbd35fd33f8d2')
    return engine


if __name__ == '__main__':
    if not sys.platform.startswith('linux'):
        raise SystemExit('Run on test-env')
    load()['main']()
