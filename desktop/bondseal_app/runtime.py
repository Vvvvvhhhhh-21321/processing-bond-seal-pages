from __future__ import annotations

import sys
from pathlib import Path


def ensure_core_importable() -> None:
    """Expose the canonical Skill package in a checkout; PyInstaller bundles it."""
    if getattr(sys, "frozen", False):
        return
    repository_root = Path(__file__).resolve().parents[2]
    core_scripts = (
        repository_root
        / ".agents"
        / "skills"
        / "processing-bond-seal-pages"
        / "scripts"
    )
    if core_scripts.is_dir() and str(core_scripts) not in sys.path:
        sys.path.insert(0, str(core_scripts))


def core_hidden_imports() -> list[str]:
    """Hidden imports for the quick-collect, batch-import, and Word worker flows."""
    return [
        "bond_seal_pages.batch_contract",
        "bond_seal_pages.cli",
        "bond_seal_pages.pdf_ops",
        "bond_seal_pages.project_workflow",
        "bond_seal_pages.selected_batch",
        "bond_seal_pages.word_conversion",
        "bond_seal_pages.word_worker",
        "pythoncom",
        "pywintypes",
        "win32timezone",
        "win32com.client",
    ]
