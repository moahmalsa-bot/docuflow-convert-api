import io
import unittest

import fitz
from PIL import Image

from app.services.image_detection import detect_embedded_images
from app.services.layout_analysis import infer_ocr_tables, merge_ocr_text
from app.services.ocr import normalize_ocr_languages, page_needs_ocr, script_profile


class OcrArabicLayoutTests(unittest.TestCase):
    def test_default_languages_include_arabic_and_english(self) -> None:
        self.assertEqual(normalize_ocr_languages("ara+eng"), "ara+eng")
        self.assertEqual(normalize_ocr_languages("ara,eng,ara"), "ara+eng")

    def test_script_profile_arabic_english_and_mixed(self) -> None:
        self.assertEqual(script_profile("مرحبا بالعالم")["direction"], "rtl")
        self.assertEqual(script_profile("Hello world")["direction"], "ltr")
        mixed = script_profile("رقم Invoice 123")
        self.assertEqual(mixed["language"], "mixed")
        self.assertEqual(mixed["direction"], "mixed")

    def test_full_page_scan_with_tiny_native_text_still_needs_ocr(self) -> None:
        image = Image.new("RGB", (600, 800), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")

        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=stream.getvalue())
        page.insert_text((20, 790), "1", fontsize=8)
        self.assertTrue(page_needs_ocr(page))

    def test_native_text_page_does_not_need_ocr(self) -> None:
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_text((40, 100), "This is a normal searchable document with enough native text for analysis.", fontsize=12)
        self.assertFalse(page_needs_ocr(page))

    def test_embedded_image_contains_scan_metadata(self) -> None:
        image = Image.new("RGB", (600, 800), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        document = fitz.open()
        page = document.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=stream.getvalue())

        detected = detect_embedded_images(page, 1)
        self.assertEqual(len(detected), 1)
        self.assertTrue(detected[0]["metadata"]["is_page_scan_candidate"])
        self.assertGreaterEqual(detected[0]["metadata"]["page_coverage"], 0.99)

    def test_scanned_table_is_inferred_from_aligned_ocr_cells(self) -> None:
        rows = [
            [("الاسم", [40, 80, 90, 100]), ("Name", [180, 80, 230, 100]), ("Amount", [360, 80, 420, 100])],
            [("أحمد", [40, 115, 90, 135]), ("Ahmed", [180, 115, 235, 135]), ("10", [360, 115, 380, 135])],
            [("سالم", [40, 150, 90, 170]), ("Salim", [180, 150, 230, 170]), ("20", [360, 150, 380, 170])],
        ]
        ocr = []
        for index, row in enumerate(rows, start=1):
            words = [{"text": text, "bounding_box": box, "confidence": 0.95} for text, box in row]
            ocr.append({
                "object_id": f"p1-ocr-{index}",
                "object_type": "text",
                "page_number": 1,
                "bounding_box": [40, row[0][1][1], 420, row[0][1][3]],
                "text": " ".join(text for text, _ in row),
                "metadata": {"words": words},
            })

        tables = infer_ocr_tables(ocr, 1, 600, 800)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["metadata"]["row_count"], 3)
        self.assertEqual(tables[0]["metadata"]["column_count"], 3)
        self.assertIn("Ahmed", tables[0]["text"])

    def test_ocr_native_duplicate_is_removed(self) -> None:
        native = [{
            "object_id": "p1-text-1",
            "object_type": "text",
            "page_number": 1,
            "bounding_box": [10, 10, 100, 30],
            "text": "Hello",
        }]
        ocr = [{
            "object_id": "p1-ocr-1",
            "object_type": "text",
            "page_number": 1,
            "bounding_box": [10, 10, 100, 30],
            "text": "Hello",
        }]
        self.assertEqual(merge_ocr_text(native, ocr), [])


if __name__ == "__main__":
    unittest.main()
