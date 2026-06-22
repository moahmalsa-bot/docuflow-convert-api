import os
import tempfile
import unittest


try:
    import fitz
    from fastapi.testclient import TestClient

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

    @staticmethod
    def _sample_pdf() -> bytes:
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 120), "Sample contract text", fontsize=12)
        return document.tobytes()


if __name__ == "__main__":
    unittest.main()
