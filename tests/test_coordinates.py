import unittest

from app.utils.coordinates import denormalize_box, normalized_box, render_dimensions, selection_box_from_normalized


class CoordinateTests(unittest.TestCase):
    def test_normalized_box_maps_to_rendered_page_coordinates(self) -> None:
        page_width = 595
        page_height = 842
        render_width, render_height = render_dimensions(page_width, page_height, dpi=144)
        source_box = [59.5, 84.2, 178.5, 210.5]

        normalized = normalized_box(source_box, page_width, page_height)
        rendered_box = denormalize_box(normalized, render_width, render_height)

        self.assertEqual((render_width, render_height), (1190, 1684))
        self.assertAlmostEqual(rendered_box[0], 119.0, places=3)
        self.assertAlmostEqual(rendered_box[1], 168.4, places=3)
        self.assertAlmostEqual(rendered_box[2], 357.0, places=3)
        self.assertAlmostEqual(rendered_box[3], 421.0, places=3)

    def test_normalized_box_values_are_clamped_between_zero_and_one(self) -> None:
        normalized = normalized_box([-10, 10, 120, 220], page_width=100, page_height=200)

        for value in normalized.values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 1)
        self.assertLessEqual(normalized["left"] + normalized["width"], 1)
        self.assertLessEqual(normalized["top"] + normalized["height"], 1)

    def test_selection_mapping_round_trips_to_pdf_coordinates(self) -> None:
        source_box = [72, 120, 240, 156]
        normalized = normalized_box(source_box, page_width=612, page_height=792)

        mapped = selection_box_from_normalized(normalized, page_width=612, page_height=792)

        for actual, expected in zip(mapped, source_box):
            self.assertAlmostEqual(actual, expected, places=2)


if __name__ == "__main__":
    unittest.main()

