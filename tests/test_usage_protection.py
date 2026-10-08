import asyncio
import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import fitz
from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter

from app.utils import files as file_utils
from app.utils.files import ConversionError, cleanup_stale_temp_jobs, run_command, save_upload, validate_upload_filename
from app.utils.validation import validate_pdf_batch_page_count, validate_pdf_page_count


class UsageProtectionTests(unittest.TestCase):
    def test_dangerous_upload_filenames_are_rejected(self) -> None:
        for filename in ("../secret.pdf", "..\\secret.pdf", ".hidden.pdf", "bad\x00name.pdf", " report.pdf"):
            with self.subTest(filename=repr(filename)):
                with self.assertRaises(HTTPException) as ctx:
                    validate_upload_filename(filename, "PDF")
                self.assertEqual(ctx.exception.status_code, 400)

    def test_oversized_upload_is_rejected_and_partial_file_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_limit = file_utils.MAX_UPLOAD_BYTES
            file_utils.MAX_UPLOAD_BYTES = 3
            try:
                upload = UploadFile(filename="safe.pdf", file=io.BytesIO(b"1234"))
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(save_upload(upload, Path(temp_dir), [".pdf"], "PDF"))
                self.assertEqual(ctx.exception.status_code, 413)
                self.assertEqual(list(Path(temp_dir).iterdir()), [])
            finally:
                file_utils.MAX_UPLOAD_BYTES = original_limit

    def test_pdf_page_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "pages.pdf"
            document = fitz.open()
            for _ in range(3):
                document.new_page()
            document.save(path)
            document.close()

            with self.assertRaises(HTTPException) as ctx:
                validate_pdf_page_count(path, max_pages=2)
            self.assertEqual(ctx.exception.status_code, 413)

    def test_encrypted_pdf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "encrypted.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            writer.encrypt("secret")
            with path.open("wb") as file_obj:
                writer.write(file_obj)

            with self.assertRaises(HTTPException) as ctx:
                validate_pdf_page_count(path)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("Encrypted", str(ctx.exception.detail))

    def test_corrupted_pdf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken.pdf"
            path.write_bytes(b"%PDF-1.7\nthis is not a real PDF")
            with self.assertRaises(HTTPException) as ctx:
                validate_pdf_page_count(path)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_merged_page_limit_is_enforced(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            validate_pdf_batch_page_count([200, 201], max_pages=400)
        self.assertEqual(ctx.exception.status_code, 413)

    def test_command_timeout_is_enforced(self) -> None:
        with self.assertRaises(ConversionError) as ctx:
            run_command([sys.executable, "-c", "import time; time.sleep(1)"], timeout=0.05)
        self.assertIn("timed out", str(ctx.exception))

    def test_stale_temp_jobs_are_removed_without_touching_other_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            stale = root / "docuflow-stale"
            keep = root / "not-docuflow"
            stale.mkdir()
            keep.mkdir()
            old = time.time() - 3600
            os.utime(stale, (old, old))
            os.utime(keep, (old, old))

            removed = cleanup_stale_temp_jobs(max_age_seconds=60, temp_root=root)

            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(keep.exists())


if __name__ == "__main__":
    unittest.main()
