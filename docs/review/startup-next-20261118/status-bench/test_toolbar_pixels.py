"""Host-only regression for the native toolbar qualification region."""

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image


spec = importlib.util.spec_from_file_location('toolbar_roi_test_target', Path(__file__).with_name('toolbar_pixels.py'))
roi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roi)


class ToolbarPixels(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix='toolbar-pixels-test-'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.geometry = {'width': 930, 'height': 1240, 'status_bar_excluded_pixels': 36}
        self.meta = {'profile':'scribe','landscape':False,'rotation_degrees':0,'backend':'kindle',
                     'route':'library','window_size':[930,1240]}
        for side in ('baseline','candidate'):
            d = self.case(side); d.mkdir(parents=True)
            (d/'final.metadata.json').write_text(json.dumps(self.meta))
            self.write(side, [(823,14)])

    def case(self, side):
        return self.root/'outputs/out'/(side+'-qualify')

    def write(self, side, ink):
        image = Image.new('L',(930,1240),255)
        for p in ink: image.putpixel(p,0)
        image.save(self.case(side)/'native-settled.ppm',format='PPM')

    def test_equal_or_clock_change_only(self):
        for additional in ([],[(44,12)]):
            self.write('candidate',[(823,14),*additional]);r=roi.compare(self.root,self.geometry)
            self.assertTrue(r['identical_right_status_bar'] and r['right_status_bar_observable'])
            self.assertEqual(r['right_status_bar_region'],{'x':465,'y':0,'width':465,'height':36})

    def test_single_wifi_or_battery_pixel_changes_block_qualification(self):
        for p in ((815,12),(917,17)):
            self.write('candidate',[(823,14),p]);r=roi.compare(self.root,self.geometry)
            self.assertFalse(r['identical_right_status_bar']);self.assertTrue(r['right_status_bar_observable'])

    def test_both_blank_regions_are_not_rendering_evidence(self):
        for side in ('baseline','candidate'):self.write(side,[])
        r=roi.compare(self.root,self.geometry)
        self.assertTrue(r['identical_right_status_bar']);self.assertFalse(r['right_status_bar_observable'])

    def test_bad_geometry_or_stale_route_rejected(self):
        with self.assertRaises(ValueError):roi.compare(self.root,{**self.geometry,'status_bar_excluded_pixels':35})
        (self.case('candidate')/'final.metadata.json').write_text(json.dumps({**self.meta,'route':'settings'}))
        with self.assertRaises(ValueError):roi.compare(self.root,self.geometry)

    def test_historical_native_frames(self):
        d=Path('/root/aidoku-next-coldbench-20261118/private/aidoku-next-cold-pixels-141a0865')
        r=roi.compare(d,{'width':1860,'height':2480,'status_bar_excluded_pixels':72})
        self.assertTrue(r['identical_right_status_bar'] and r['right_status_bar_observable'])
        self.assertEqual(r['right_status_bar_region'],{'x':930,'y':0,'width':930,'height':72})


if __name__ == '__main__':
    unittest.main()
