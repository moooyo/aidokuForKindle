import os
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from . import review_reference as review


class PositiveNegativeControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        baseline = Path(os.environ["AIDOKU_REVIEW_BASELINES"])
        cls.library = review.load_public(baseline / "library.png")
        cls.other = review.load_public(baseline / "navigation.png")

    def shelf(self, count):
        image = self.library.copy()
        painter = ImageDraw.Draw(image)
        _, grid = review.observer.regions(15)
        for rectangle in grid[1 + count:]:
            left, top, right, bottom = review.box(rectangle)
            painter.rectangle((left - 1, top - 1, right + 1, bottom + 32), fill=255)
        return image

    def test_direction_independently_of_grid_count_and_no_fixed_book_count(self):
        for count, turn in ((1, 180), (6, 0), (12, 180), (15, 0)):
            with self.subTest(count=count, turn=turn):
                image = self.shelf(count)
                if turn:
                    image = image.transpose(Image.Transpose.ROTATE_180)
                raw = image.resize((1860, 2480), Image.Resampling.NEAREST).tobytes()
                direction, image = review.identify_orientation(raw, self.library, self.other)
                self.assertEqual(direction, turn)
                self.assertEqual(review.occupancy(image), count)

    def test_wrong_route_and_featureless_frames(self):
        for image in (self.other, Image.new("L", review.LOGICAL, 255)):
            with self.assertRaisesRegex(ValueError, "library_chrome"):
                review.identify_orientation(image.resize((1860, 2480)).tobytes(), self.library, self.other)

    def test_hole_placeholder_empty_shelf_and_missing_caption_do_not_create_rule(self):
        cover = review.box(review.observer.regions(15)[1][3])
        left, top, right, bottom = cover
        image = self.shelf(6)
        ImageDraw.Draw(image).rectangle((left - 1, top - 1, right + 1, bottom + 32), fill=255)
        with self.assertRaisesRegex(ValueError, "noncontiguous"):
            review.occupancy(image)
        image = self.shelf(6)
        painter = ImageDraw.Draw(image)
        painter.rectangle((left + 2, top + 2, right - 2, bottom - 2), fill=255)
        painter.text((left + 14, top + 14), "Placeholder title", fill=0)
        with self.assertRaisesRegex(ValueError, "unproven_art"):
            review.occupancy(image)
        image = self.shelf(6)
        ImageDraw.Draw(image).rectangle((left - 1, bottom + 1, right + 1, bottom + 32), fill=255)
        with self.assertRaisesRegex(ValueError, "without_a_caption"):
            review.occupancy(image)
        with self.assertRaisesRegex(ValueError, "empty_or_noncontiguous"):
            review.occupancy(self.shelf(0))


if __name__ == "__main__":
    unittest.main()
