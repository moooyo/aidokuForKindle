"""Compare only the native pixels covering the Library Wi-Fi and battery.

Run on test-env. The actual framebuffer captures stay in the private batch.
The host returns geometry and equality, never pixels, values or image hashes.
"""

import json
from pathlib import Path
import sys

from PIL import Image, ImageChops


def compare(directory, verified_pixel_row):
    if sys.platform != 'linux':
        raise ValueError('Run the framebuffer comparison on test-env')
    directory = Path(directory)
    width = verified_pixel_row.get('width')
    height = verified_pixel_row.get('height')
    top = verified_pixel_row.get('status_bar_excluded_pixels')
    if not (all(type(v) is int and v > 0 for v in (width, height, top))
            and width * height <= 12_000_000 and top < height):
        raise ValueError('unverified_toolbar_capture_geometry')
    roi = (width // 2, 0, width, top)
    crops = []
    profiles = {'scribe': (930, 1240, 36), 'pw5': (618, 824, 28)}
    for role in ('baseline', 'candidate'):
        case = directory / 'outputs/out' / (role + '-qualify')
        try:
            meta = json.loads((case / 'final.metadata.json').read_bytes())
            if not (isinstance(meta, dict) and meta.get('profile') in profiles
                    and meta.get('backend') == 'kindle' and meta.get('route') == 'library'
                    and meta.get('landscape') is False and meta.get('rotation_degrees') == 0
                    and meta.get('window_size') == [width, height]):
                raise ValueError('unverified_toolbar_capture_metadata')
            w, h, bar = profiles[meta['profile']]
            if width * h != height * w or top * h != bar * height:
                raise ValueError('unverified_toolbar_capture_geometry')
            with Image.open(case / 'native-settled.ppm') as image:
                if image.format != 'PPM' or image.size != (width, height):
                    raise ValueError('unverified_toolbar_capture_geometry')
                crops.append(image.crop(roi).convert('RGB'))
        except (OSError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, Image.DecompressionBombError):
            raise ValueError('missing_or_invalid_toolbar_capture') from None

    # The clock is left-aligned; both indicator icons and the battery label
    # are right-aligned (ui/widgets.slint, StatusBar). Identical blank crops
    # do not verify that those indicators were drawn.
    observable = all(any(lo != hi for lo, hi in crop.getextrema()) for crop in crops)
    equivalent = ImageChops.difference(crops[0], crops[1]).getbbox() is None
    return {'right_status_bar_region': {'x': roi[0], 'y': roi[1], 'width': width-roi[0], 'height': top},
            'right_status_bar_observable': observable, 'identical_right_status_bar': equivalent}
