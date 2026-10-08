from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

REQUIRED = [
    "app/main.py",
    "app/api/conversion_routes.py",
    "app/api/pdf_routes.py",
    "app/models/ai_models.py",
    "app/models/pdf_models.py",
    "app/services/ai_planner.py",
    "app/services/document_history.py",
    "app/services/pdf_analysis.py",
    "app/services/pdf_edit.py",
    "app/services/pdf_render.py",
    "app/utils/files.py",
    "app/utils/validation.py",
    "Dockerfile",
    "requirements.txt",
]


def fail(message: str) -> None:
    raise AssertionError(message)


for relative in REQUIRED:
    if not (ROOT / relative).exists():
        fail(f"Required architecture file missing: {relative}")

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
if main.count("app.include_router(conversion_router)") != 1:
    fail("conversion_router must be registered exactly once")
if main.count("app.include_router(pdf_router)") != 1:
    fail("pdf_router must be registered exactly once")
if "allow_credentials=False" not in main:
    fail("CORS must not enable credentials while wildcard origins are used")

# Dependency direction: lower layers must never import HTTP/router layers.
for path in (APP / "services").glob("*.py"):
    text = path.read_text(encoding="utf-8")
    if "from app.api" in text or "import app.api" in text or "from app.main" in text:
        fail(f"Service imports HTTP layer: {path.relative_to(ROOT)}")

for path in (APP / "models").glob("*.py"):
    text = path.read_text(encoding="utf-8")
    if any(token in text for token in ("from app.api", "from app.services", "from app.main")):
        fail(f"Model imports a higher layer: {path.relative_to(ROOT)}")

# Parse every Python file and reject duplicated top-level imports.
for path in list(APP.rglob("*.py")) + list((ROOT / "tests").glob("*.py")):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            key = ast.dump(node, include_attributes=False)
            if key in seen:
                fail(f"Duplicate top-level import in {path.relative_to(ROOT)}")
            seen.add(key)

# Security/build invariants.
for path in APP.rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    if "os.system(" in text:
        fail(f"os.system is forbidden: {path.relative_to(ROOT)}")
    if "shell=True" in text:
        fail(f"shell=True is forbidden: {path.relative_to(ROOT)}")

print("Architecture contracts: PASS")
