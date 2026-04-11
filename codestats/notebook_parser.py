"""Jupyter notebook parser -- extract code cells."""

from __future__ import annotations

import copy
import json
import logging

log = logging.getLogger(__name__)


def parse_notebook(file_info, source: bytes, ast_parser):
    """Parse .ipynb by extracting code cells."""
    from .models import ParsedFile

    try:
        nb = json.loads(source)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ParsedFile(
            file_info=file_info,
            symbols=[],
            imports=[],
            exports=[],
            docstring=None,
            parse_errors=["Invalid notebook JSON"],
            content_hash="",
        )

    lang = _detect_kernel_language(nb)
    cells = _extract_code_cells(nb)
    if not cells or lang == "unknown":
        return ParsedFile(
            file_info=file_info,
            symbols=[],
            imports=[],
            exports=[],
            docstring=None,
            parse_errors=[],
            content_hash="",
        )

    # Concatenate all code cells
    combined = "\n".join(c["source"] for c in cells)

    cell_info = copy.copy(file_info)
    cell_info.language = lang

    result = ast_parser.parse_file(cell_info, combined.encode("utf-8"))
    return ParsedFile(
        file_info=file_info,
        symbols=result.symbols,
        imports=result.imports,
        exports=result.exports,
        docstring=result.docstring,
        parse_errors=result.parse_errors,
        content_hash="",
    )


def _detect_kernel_language(nb: dict) -> str:
    try:
        return (
            nb.get("metadata", {})
            .get("kernelspec", {})
            .get("language", "python")
            .lower()
        )
    except Exception:
        return "python"


def _extract_code_cells(nb: dict) -> list[dict]:
    cells = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            src = cell.get("source", [])
            if isinstance(src, list):
                src = "".join(src)
            cells.append({"source": src})
    return cells
