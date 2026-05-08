"""Extrae texto plano de un PDF (pypdf)."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


def extract_text(pdf_path: str | Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip()
