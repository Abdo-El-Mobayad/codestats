"""Vue Single File Component parser -- extract <script> blocks."""

from __future__ import annotations

import copy
import logging
import re

log = logging.getLogger(__name__)

_SCRIPT_RE = re.compile(
    r"<script\b([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE
)


def extract_vue_scripts(source: str) -> list[dict]:
    """Returns: [{content, lang, line_offset, is_setup}]"""
    results = []
    for m in _SCRIPT_RE.finditer(source):
        attrs = m.group(1)
        content = m.group(2)
        line_offset = source[: m.start(2)].count("\n")
        lang = (
            "ts"
            if 'lang="ts"' in attrs or "lang='ts'" in attrs
            else "js"
        )
        is_setup = "setup" in attrs
        results.append(
            {
                "content": content,
                "lang": lang,
                "line_offset": line_offset,
                "is_setup": is_setup,
            }
        )
    return results


def parse_vue_file(file_info, source: bytes, ast_parser):
    """Parse .vue file by extracting and re-parsing script blocks."""
    from .models import ParsedFile

    text = source.decode("utf-8", errors="replace")
    scripts = extract_vue_scripts(text)
    if not scripts:
        return ParsedFile(
            file_info=file_info,
            symbols=[],
            imports=[],
            exports=[],
            docstring=None,
            parse_errors=[],
            content_hash="",
        )

    # Parse the first script block
    script = scripts[0]
    lang_tag = "typescript" if script["lang"] == "ts" else "javascript"

    # Create a temporary FileInfo for the script content
    script_info = copy.copy(file_info)
    script_info.language = lang_tag

    script_bytes = script["content"].encode("utf-8")
    result = ast_parser.parse_file(script_info, script_bytes)

    # Adjust line numbers by the script block offset
    offset = script["line_offset"]
    for sym in result.symbols:
        sym.start_line += offset
        sym.end_line += offset

    return ParsedFile(
        file_info=file_info,
        symbols=result.symbols,
        imports=result.imports,
        exports=result.exports,
        docstring=result.docstring,
        parse_errors=result.parse_errors,
        content_hash="",
    )
