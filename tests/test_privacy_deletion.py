import os
import tempfile
import unittest

import fitz
from fastapi import HTTPException

from app.services.document_history import create_document_dir, delete_document, initialize_document


class PrivacyDeletionTests(unittest.TestCase):
    def test_document_delete_token_removes_session_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            previous = os.environ.get("DOCUFLOW_DOCUMENT_STORE")
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = root
            try:
                document_id, directory = create_document_dir()
                source = directory / "source.pdf"
                pdf = fitz.open()
                pdf.new_page()
                pdf.save(source)
                pdf.close()

                response = initialize_document(
                    document_id,
                    source,
                    "private.pdf",
                    {
                        "page_count": 1,
                        "scanned_pages": [],
                        "detected_languages": [],
                        "ocr_languages": [],
                        "pages": [],
                        "objects": [],
                        "text_object_count": 0,
                        "image_object_count": 0,
                        "table_object_count": 0,
                    },
                )
                self.assertFalse(response["training_use"])
                self.assertEqual(response["retention_hours"], 24)
                self.assertTrue(response["delete_token"])
                self.assertTrue(directory.exists())

                deleted = delete_document(document_id, response["delete_token"])
                self.assertTrue(deleted["deleted"])
                self.assertFalse(directory.exists())
            finally:
                if previous is None:
                    os.environ.pop("DOCUFLOW_DOCUMENT_STORE", None)
                else:
                    os.environ["DOCUFLOW_DOCUMENT_STORE"] = previous

    def test_wrong_delete_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            previous = os.environ.get("DOCUFLOW_DOCUMENT_STORE")
            os.environ["DOCUFLOW_DOCUMENT_STORE"] = root
            try:
                document_id, directory = create_document_dir()
                source = directory / "source.pdf"
                pdf = fitz.open()
                pdf.new_page()
                pdf.save(source)
                pdf.close()
                initialize_document(
                    document_id,
                    source,
                    "private.pdf",
                    {
                        "page_count": 1,
                        "scanned_pages": [],
                        "detected_languages": [],
                        "ocr_languages": [],
                        "pages": [],
                        "objects": [],
                        "text_object_count": 0,
                        "image_object_count": 0,
                        "table_object_count": 0,
                    },
                )
                with self.assertRaises(HTTPException) as ctx:
                    delete_document(document_id, "wrong-token")
                self.assertEqual(ctx.exception.status_code, 403)
                self.assertTrue(directory.exists())
            finally:
                if previous is None:
                    os.environ.pop("DOCUFLOW_DOCUMENT_STORE", None)
                else:
                    os.environ["DOCUFLOW_DOCUMENT_STORE"] = previous


if __name__ == "__main__":
    unittest.main()
