import hashlib
import io
import os
import tempfile
import unittest


try:
    import fitz
    from fastapi.testclient import TestClient
    from PIL import Image, ImageDraw

    HAS_INTEGRATION_DEPS = True
except Exception:
    HAS_INTEGRATION_DEPS = False


@unittest.skipUnless(HAS_INTEGRATION_DEPS, "FastAPI/PyMuPDF dependencies are not installed")
class PdfIntegrationTests(unittest.TestCase):
    def test_analyze_render_apply_download_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = temp_dir

            from app.main import app

            client = TestClient(app)
            pdf_bytes = self._sample_pdf()

            analyze = client.post("/pdf/analyze", files={"file": ("sample.pdf", pdf_bytes, "application/pdf")})
            self.assertEqual(analyze.status_code, 200, analyze.text)
            payload = analyze.json()
            self.assertIn("pages", payload)
            self.assertGreaterEqual(payload["text_object_count"], 1)

            document_id = payload["document_id"]
            text_object = next(item for item in payload["objects"] if item["object_type"] == "text")

            render = client.get(f"/pdf/render/{document_id}/1")
            self.assertEqual(render.status_code, 200, render.text)
            self.assertEqual(render.headers["content-type"], "image/png")

            apply = client.post(
                "/pdf/ai-edit/apply",
                json={
                    "document_id": document_id,
                    "operations": [{"type": "highlight", "object_id": text_object["object_id"]}],
                },
            )
            self.assertEqual(apply.status_code, 200, apply.text)
            edited = apply.json()
            self.assertIn("downloadUrl", edited)

            download = client.get(edited["downloadUrl"])
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.headers["content-type"], "application/pdf")
            self.assertGreater(len(download.content), 100)

    def test_base44_replace_text_payload_creates_new_version_without_overwriting_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = temp_dir

            from app.main import app
            from app.services.document_history import history, original_pdf_path

            client = TestClient(app)
            original_text = "ABB - OPEN - 60 FSA-2021-32"
            replacement_text = "ABB - BLOCKED - 60 FSA-2021-32"
            pdf_bytes = self._two_block_pdf(original_text)

            analyze = client.post("/pdf/analyze", files={"file": ("base44.pdf", pdf_bytes, "application/pdf")})
            self.assertEqual(analyze.status_code, 200, analyze.text)
            analysis = analyze.json()
            document_id = analysis["document_id"]
            self.assertTrue(any(item["object_id"] == "p1-text-2" for item in analysis["objects"]))

            apply = client.post(
                "/pdf/ai-edit/apply",
                json={
                    "document_id": document_id,
                    "operations": [
                        {
                            "operation": "replace_text",
                            "object_id": "p1-text-2",
                            "page": 1,
                            "new_text": replacement_text,
                            "preserve_style": True,
                            "fit_mode": "shrink_to_fit",
                        }
                    ],
                },
            )
            self.assertEqual(apply.status_code, 200, apply.text)
            response = apply.json()
            self.assertEqual(response["document_id"], document_id)
            self.assertIn("downloadUrl", response)
            self.assertEqual(response["version_id"], "v0001")

            document_history = history(document_id)
            self.assertEqual(len(document_history["versions"]), 2)
            self.assertEqual(document_history["versions"][-1]["operations"][0]["operation"], "replace_text")
            self.assertEqual(document_history["versions"][-1]["operations"][0]["new_text"], replacement_text)

            original_pdf = fitz.open(original_pdf_path(document_id))
            self.assertIn(original_text, original_pdf[0].get_text())
            original_pdf.close()

            download = client.get(response["downloadUrl"])
            self.assertEqual(download.status_code, 200, download.text)
            edited_pdf = fitz.open(stream=download.content, filetype="pdf")
            edited_text = edited_pdf[0].get_text()
            edited_digest = self._page_digest(edited_pdf[0])
            edited_pdf.close()

            self.assertIn(replacement_text, edited_text)
            self.assertNotEqual(self._pdf_page_digest(pdf_bytes), edited_digest)

    def test_replace_text_visually_replaces_and_downloads_edited_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = temp_dir

            from app.main import app

            client = TestClient(app)
            original_text = "ORIGINAL TEXT"
            replacement_text = "NEW TEXT"
            pdf_bytes = self._single_text_pdf(original_text)

            analyze = client.post("/pdf/analyze", files={"file": ("replace.pdf", pdf_bytes, "application/pdf")})
            self.assertEqual(analyze.status_code, 200, analyze.text)
            analysis = analyze.json()
            document_id = analysis["document_id"]
            text_object = next(item for item in analysis["objects"] if item["object_type"] == "text" and original_text in item["text"])

            apply = client.post(
                "/pdf/ai-edit/apply",
                json={
                    "document_id": document_id,
                    "operations": [
                        {
                            "operation": "replace_text",
                            "object_id": text_object["object_id"],
                            "page": 1,
                            "new_text": replacement_text,
                            "preserve_style": True,
                            "fit_mode": "shrink_to_fit",
                        }
                    ],
                },
            )
            self.assertEqual(apply.status_code, 200, apply.text)
            response = apply.json()
            self.assertEqual(response["document_id"], document_id)
            self.assertEqual(response["version_id"], "v0001")
            self.assertIn("downloadUrl", response)

            download = client.get(response["downloadUrl"])
            self.assertEqual(download.status_code, 200, download.text)
            self.assertEqual(download.headers["content-type"], "application/pdf")
            self.assertNotEqual(download.content, pdf_bytes)

            edited_pdf = fitz.open(stream=download.content, filetype="pdf")
            edited_text = edited_pdf[0].get_text()
            edited_digest = self._page_digest(edited_pdf[0])
            pixmap = edited_pdf[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            edited_pdf.close()

            self.assertIn(replacement_text, edited_text)
            self.assertNotEqual(self._pdf_page_digest(pdf_bytes), edited_digest)
            self.assertGreater(len(pixmap.samples), 0)

    def test_replace_text_visually_edits_image_based_pdf_with_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = temp_dir

            from app.main import app
            from app.services import pdf_analysis

            client = TestClient(app)
            pdf_bytes = self._image_based_pdf("OLD TOTAL")
            original_digest = self._pdf_page_digest(pdf_bytes)

            original_ocr_page = pdf_analysis.ocr_page
            pdf_analysis.ocr_page = lambda page, page_number, document_dir: []
            try:
                analyze = client.post("/pdf/analyze", files={"file": ("receipt.pdf", pdf_bytes, "application/pdf")})
                self.assertEqual(analyze.status_code, 200, analyze.text)
            finally:
                pdf_analysis.ocr_page = original_ocr_page
            document_id = analyze.json()["document_id"]

            apply = client.post(
                "/pdf/ai-edit/apply",
                json={
                    "document_id": document_id,
                    "operations": [
                        {
                            "operation": "replace_text",
                            "page": 1,
                            "bounding_box": [68, 90, 210, 122],
                            "new_text": "NEW TOTAL",
                            "font_size": 18,
                            "fit_mode": "shrink_to_fit",
                        }
                    ],
                },
            )
            self.assertEqual(apply.status_code, 200, apply.text)
            response = apply.json()
            self.assertIn("downloadUrl", response)

            download = client.get(response["downloadUrl"])
            self.assertEqual(download.status_code, 200, download.text)
            edited_pdf = fitz.open(stream=download.content, filetype="pdf")
            edited_digest = self._page_digest(edited_pdf[0])
            edited_pdf.close()

            self.assertNotEqual(original_digest, edited_digest)

    def test_ai_plan_change_text_single_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = temp_dir

            from app.main import app

            client = TestClient(app)
            pdf_bytes = self._single_text_pdf("ORIGINAL TEXT")
            analyze = client.post("/pdf/analyze", files={"file": ("plan.pdf", pdf_bytes, "application/pdf")})
            self.assertEqual(analyze.status_code, 200, analyze.text)

            plan = client.post(
                "/pdf/ai-edit/plan",
                json={
                    "document_id": analyze.json()["document_id"],
                    "instruction": "Change ORIGINAL TEXT to NEW TEXT",
                    "selected_object_ids": [],
                    "scope": "document",
                },
            )
            self.assertEqual(plan.status_code, 200, plan.text)
            operations = plan.json()

            self.assertIsInstance(operations, list)
            self.assertEqual(operations[0]["operation"], "replace_text")
            self.assertEqual(operations[0]["new_text"], "NEW TEXT")
            self.assertEqual(operations[0]["object_id"], "p1-text-1")

    def test_ai_plan_change_text_requires_confirmation_for_multiple_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = temp_dir

            from app.main import app

            client = TestClient(app)
            pdf_bytes = self._two_block_pdf("DUPLICATE TEXT", first_text="DUPLICATE TEXT")
            analyze = client.post("/pdf/analyze", files={"file": ("plan-multiple.pdf", pdf_bytes, "application/pdf")})
            self.assertEqual(analyze.status_code, 200, analyze.text)

            plan = client.post(
                "/pdf/ai-edit/plan",
                json={
                    "document_id": analyze.json()["document_id"],
                    "instruction": "Change DUPLICATE TEXT to NEW TEXT",
                    "selected_object_ids": [],
                    "scope": "document",
                },
            )
            self.assertEqual(plan.status_code, 200, plan.text)
            payload = plan.json()

            self.assertTrue(payload["requires_confirmation"])
            self.assertEqual(len(payload["matches"]), 2)
            self.assertEqual(payload["operations"], [])

    @staticmethod
    def _sample_pdf() -> bytes:
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 120), "Sample contract text", fontsize=12)
        return document.tobytes()

    @staticmethod
    def _two_block_pdf(second_text: str, first_text: str = "First detectable text block") -> bytes:
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 120), first_text, fontsize=12)
        page.insert_text((72, 220), second_text, fontsize=12)
        return document.tobytes()

    @staticmethod
    def _single_text_pdf(text: str) -> bytes:
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 180), text, fontsize=14)
        return document.tobytes()

    @staticmethod
    def _image_based_pdf(text: str) -> bytes:
        image = Image.new("RGB", (612, 792), "white")
        draw = ImageDraw.Draw(image)
        draw.text((72, 96), text, fill="black")
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG")
        image_bytes.seek(0)

        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=image_bytes.read())
        return document.tobytes()

    @staticmethod
    def _pdf_page_digest(pdf_bytes: bytes) -> str:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            return PdfIntegrationTests._page_digest(document[0])
        finally:
            document.close()

    @staticmethod
    def _page_digest(page: fitz.Page) -> str:
        return hashlib.sha256(page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).samples).hexdigest()


if __name__ == "__main__":
    unittest.main()
