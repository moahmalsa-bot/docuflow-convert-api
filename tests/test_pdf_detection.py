import io
import unittest


try:
    import fitz
    from PIL import Image

    from app.services.image_detection import detect_embedded_images
    from app.services.pdf_analysis import detect_text_blocks

    HAS_PDF_DEPS = True
except Exception:
    HAS_PDF_DEPS = False


@unittest.skipUnless(HAS_PDF_DEPS, "PyMuPDF/Pillow dependencies are not installed")
class PdfDetectionTests(unittest.TestCase):
    def test_text_detection_returns_normalized_coordinates(self) -> None:
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 120), "Hello DocuFlow", fontsize=12)

        objects = detect_text_blocks(page, page_number=1)

        self.assertGreaterEqual(len(objects), 1)
        text_object = objects[0]
        self.assertEqual(text_object["object_type"], "text")
        self.assertIn("Hello DocuFlow", text_object["text"])
        self.assertEqual(text_object["page_number"], 1)
        for value in text_object["normalized_box"].values():
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 1)

    def test_image_detection_returns_embedded_image_object(self) -> None:
        image = Image.new("RGB", (80, 40), color="red")
        stream = io.BytesIO()
        image.save(stream, format="PNG")

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(100, 150, 180, 190), stream=stream.getvalue())

        objects = detect_embedded_images(page, page_number=1)

        self.assertEqual(len(objects), 1)
        image_object = objects[0]
        self.assertEqual(image_object["object_type"], "image")
        self.assertEqual(image_object["object_id"], "p1-image-1")
        self.assertEqual(image_object["bounding_box"], [100.0, 150.0, 180.0, 190.0])


if __name__ == "__main__":
    unittest.main()

