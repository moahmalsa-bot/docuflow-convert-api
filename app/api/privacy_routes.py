import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.services.document_history import document_ttl_hours


router = APIRouter()


def privacy_payload() -> dict:
    editor_hours = document_ttl_hours()
    excel_hours = max(1, int(os.getenv("OUTPUT_MAX_AGE_SECONDS", "86400")) // 3600)
    crash_hours = max(1, int(os.getenv("TEMP_JOB_TTL_HOURS", "2")))
    return {
        "service": "DocuFlow",
        "policy_version": "2026-10-08",
        "training": {
            "docuflow_uses_files_for_training": False,
            "external_ai_requires_no_training_confirmation": True,
            "statement": "DocuFlow does not use uploaded files or document content to train its own models.",
        },
        "retention": {
            "ordinary_conversion_work_files": "deleted after the response finishes",
            "crash_recovery_temp_files_max_hours": crash_hours,
            "pdf_editor_session_max_hours": editor_hours,
            "excel_download_output_max_hours": excel_hours,
            "saved_library_files": "kept until the user deletes them from the DocuFlow Library",
        },
        "deletion": {
            "pdf_editor_session": "DELETE /pdf/session/{document_id} with X-Delete-Token",
            "excel_output": "DELETE /jobs/{job_id} with X-Delete-Token",
            "delete_now_supported": True,
        },
        "ai_processing": {
            "automatic_training": False,
            "external_ai_disabled_without_no_training_confirmation": True,
            "user_triggered_only": True,
        },
    }


@router.get("/privacy")
async def privacy_json():
    return privacy_payload()


@router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy():
    p = privacy_payload()
    retention = p["retention"]
    html = f"""
    <!doctype html>
    <html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>DocuFlow Privacy Policy</title>
    <style>
      body{{font-family:system-ui,-apple-system,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;color:#1d2430;line-height:1.65}}
      h1,h2{{line-height:1.2}} .card{{border:1px solid #e4e7ec;border-radius:16px;padding:18px;margin:16px 0;background:#fafafa}}
      small{{color:#667085}}
    </style></head><body>
      <h1>DocuFlow Privacy Policy</h1>
      <small>Version {p["policy_version"]}</small>
      <div class="card"><strong>No training on your documents.</strong>
      <p>DocuFlow does not use uploaded files or document content to train its own models. External AI processing is disabled unless the configured provider has been explicitly approved for no-training use, and AI processing is only initiated by a user action.</p></div>
      <h2>How long files are kept</h2>
      <ul>
        <li>Ordinary conversion working files: <strong>{retention["ordinary_conversion_work_files"]}</strong>.</li>
        <li>Crash leftovers: automatically removed within about <strong>{retention["crash_recovery_temp_files_max_hours"]} hours</strong>.</li>
        <li>PDF editor/analyze sessions: kept for at most <strong>{retention["pdf_editor_session_max_hours"]} hours</strong>, unless you delete the session sooner.</li>
        <li>Excel conversion download copies: kept for at most <strong>{retention["excel_download_output_max_hours"]} hours</strong>, unless deleted sooner.</li>
        <li>Files explicitly saved to the DocuFlow Library remain until you delete them from your Library.</li>
      </ul>
      <h2>Delete now</h2>
      <p>DocuFlow provides an immediate delete action for processing sessions and retained conversion outputs. The app's Delete action also removes the file record from your DocuFlow Library.</p>
      <h2>Processing</h2>
      <p>Files are processed only to perform the conversion, editing, OCR, preview or AI action you request. DocuFlow does not sell document content.</p>
    </body></html>
    """
    return HTMLResponse(html)
