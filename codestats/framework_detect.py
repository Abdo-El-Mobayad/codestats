"""Framework detection for codestats.

Detects the project framework (Next.js, Vite, etc.) from package.json and
config files. Returns entry point patterns used by dead_code.py and traverser.py
to avoid false positives.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def detect_framework(repo_path: Path) -> dict:
    """Detect the project framework and return metadata.

    Returns dict with:
        framework: str | None  -- e.g. "nextjs", "vite", "django", etc.
        entry_patterns: list[str]  -- glob patterns for framework entry points
        version: str | None  -- framework version if detectable
    """
    result = {
        "framework": None,
        "entry_patterns": [],
        "version": None,
    }

    # Check package.json for JS/TS frameworks
    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            with open(package_json, encoding="utf-8") as f:
                pkg = json.load(f)
            deps = {}
            deps.update(pkg.get("dependencies", {}))
            deps.update(pkg.get("devDependencies", {}))

            if "next" in deps:
                result["framework"] = "nextjs"
                result["version"] = deps["next"]
                result["entry_patterns"] = _nextjs_entry_patterns()
                return result

            if "nuxt" in deps or "nuxt3" in deps:
                result["framework"] = "nuxt"
                result["entry_patterns"] = _nuxt_entry_patterns()
                return result

            if "vite" in deps:
                result["framework"] = "vite"
                result["entry_patterns"] = ["index.html", "src/main.*", "src/App.*"]
                return result

            if "react" in deps:
                result["framework"] = "react"
                result["entry_patterns"] = ["src/index.*", "src/App.*", "public/index.html"]
                return result

            if "vue" in deps:
                result["framework"] = "vue"
                result["entry_patterns"] = ["src/main.*", "src/App.*"]
                return result

            if "svelte" in deps or "@sveltejs/kit" in deps:
                result["framework"] = "svelte"
                result["entry_patterns"] = [
                    "src/routes/**/+page.svelte",
                    "src/routes/**/+layout.svelte",
                    "src/routes/**/+server.*",
                ]
                return result

        except Exception as exc:
            log.debug("Failed to read package.json: %s", exc)

    # Check for Python frameworks
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "django" in content.lower():
                result["framework"] = "django"
                result["entry_patterns"] = [
                    "**/urls.py", "**/views.py", "**/admin.py",
                    "**/models.py", "**/forms.py", "**/serializers.py",
                    "manage.py", "**/wsgi.py", "**/asgi.py",
                ]
                return result
            if "fastapi" in content.lower():
                result["framework"] = "fastapi"
                result["entry_patterns"] = ["main.py", "app.py", "**/routers/*.py"]
                return result
            if "flask" in content.lower():
                result["framework"] = "flask"
                result["entry_patterns"] = ["app.py", "wsgi.py", "**/routes/*.py"]
                return result
        except Exception:
            pass

    return result


def _nextjs_entry_patterns() -> list[str]:
    """Return glob patterns for Next.js framework entry points."""
    return [
        # App Router
        "**/app/**/page.tsx", "**/app/**/page.ts",
        "**/app/**/page.jsx", "**/app/**/page.js",
        "**/app/**/route.tsx", "**/app/**/route.ts",
        "**/app/**/route.jsx", "**/app/**/route.js",
        "**/app/**/layout.tsx", "**/app/**/layout.ts",
        "**/app/**/layout.jsx", "**/app/**/layout.js",
        "**/app/**/loading.tsx", "**/app/**/loading.ts",
        "**/app/**/error.tsx", "**/app/**/error.ts",
        "**/app/**/not-found.tsx", "**/app/**/not-found.ts",
        "**/app/**/template.tsx", "**/app/**/template.ts",
        "**/app/**/default.tsx", "**/app/**/default.ts",
        "**/app/**/global-error.tsx",
        "**/app/**/opengraph-image.tsx",
        "**/app/**/twitter-image.tsx",
        "**/app/**/sitemap.ts",
        "**/app/**/robots.ts",
        # Pages Router
        "**/pages/**/*.tsx", "**/pages/**/*.ts",
        "**/pages/**/*.jsx", "**/pages/**/*.js",
        # Root config
        "middleware.ts", "middleware.js",
        "instrumentation.ts", "instrumentation.js",
        "next.config.*",
    ]


def _nuxt_entry_patterns() -> list[str]:
    """Return glob patterns for Nuxt framework entry points."""
    return [
        "**/pages/**/*.vue",
        "**/layouts/**/*.vue",
        "**/middleware/**/*.ts",
        "**/server/api/**/*.ts",
        "**/server/routes/**/*.ts",
        "**/plugins/**/*.ts",
        "nuxt.config.*",
    ]


def get_framework_entry_files(repo_path: Path) -> set[str]:
    """Return set of relative file paths that are framework entry points.

    Uses detect_framework() to find patterns, then matches against actual files.
    """
    info = detect_framework(repo_path)
    if not info["entry_patterns"]:
        return set()

    entry_files: set[str] = set()
    for pattern in info["entry_patterns"]:
        for match in repo_path.glob(pattern):
            if match.is_file():
                try:
                    rel = match.relative_to(repo_path).as_posix()
                    entry_files.add(rel)
                except ValueError:
                    pass

    if entry_files:
        log.info(
            "Framework '%s' detected: %d entry point files",
            info["framework"],
            len(entry_files),
        )

    return entry_files
