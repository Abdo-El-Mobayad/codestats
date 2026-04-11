"""Test scoring engine and HTML report generator.

Runs the test suite, categorizes results, computes weighted scores,
and generates a self-contained HTML report with visual progress bars
and feature badges.

Works with or without pytest-json-report by falling back to stdout parsing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CATEGORIES = {
    "correctness": {"weight": 0.25, "description": "Core features work as specified"},
    "coverage": {"weight": 0.20, "description": "Test coverage of feature surface area"},
    "edge_cases": {"weight": 0.10, "description": "Boundary conditions and unusual inputs"},
    "integration": {"weight": 0.15, "description": "Features compose correctly"},
    "regression": {"weight": 0.15, "description": "Existing features unbroken"},
    "robustness": {"weight": 0.15, "description": "Graceful failures, meaningful errors"},
}

TEST_CATEGORY_MAP = {
    "test_traverser": ["correctness", "edge_cases", "coverage"],
    "test_parser": ["correctness", "edge_cases", "coverage"],
    "test_graph": ["correctness", "integration", "coverage"],
    "test_storage": ["correctness", "regression", "coverage"],
    "test_impact": ["correctness", "integration", "robustness"],
    "test_flows": ["correctness", "coverage"],
    "test_communities": ["correctness", "coverage"],
    "test_search": ["correctness", "edge_cases", "coverage"],
    "test_dead_code": ["correctness", "edge_cases", "regression", "robustness"],
    "test_cli": ["integration", "regression", "robustness"],
    "test_visualization": ["correctness", "coverage"],
    "test_wiki": ["correctness", "coverage"],
    "test_vue_parser": ["correctness", "edge_cases", "robustness"],
    "test_notebook_parser": ["correctness", "edge_cases", "robustness"],
}


def run_tests_and_score() -> dict:
    """Run pytest and compute scores.

    Tries json-report plugin first, falls back to stdout parsing.
    """
    project_root = Path(__file__).parent.parent
    codestats_dir = project_root / ".codestats"
    codestats_dir.mkdir(parents=True, exist_ok=True)
    json_report = codestats_dir / "test-results.json"

    # Try with json-report plugin first
    cmd = [
        sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short",
        "--json-report", f"--json-report-file={json_report}",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(project_root),
    )

    tests: list[dict] = []

    if json_report.exists():
        try:
            data = json.loads(json_report.read_text(encoding="utf-8"))
            for t in data.get("tests", []):
                node_id = t.get("nodeid", "")
                outcome = t.get("outcome", "failed")
                tests.append({
                    "nodeid": node_id,
                    "passed": outcome == "passed",
                    "outcome": outcome,
                    "duration": t.get("duration", 0),
                })
        except (json.JSONDecodeError, KeyError):
            tests = _parse_pytest_stdout(result.stdout)
    else:
        tests = _parse_pytest_stdout(result.stdout)

    # If json-report didn't work and stdout parsing failed, try stderr
    # (some pytest configs send verbose output to stderr)
    if not tests and result.stderr:
        tests = _parse_pytest_stdout(result.stderr)

    # Last resort: run without json-report to get clean stdout
    if not tests:
        fallback_cmd = [
            sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short",
        ]
        fallback_result = subprocess.run(
            fallback_cmd, capture_output=True, text=True,
            cwd=str(project_root),
        )
        combined_output = fallback_result.stdout + "\n" + fallback_result.stderr
        tests = _parse_pytest_stdout(combined_output)

    # Compute per-category scores
    category_scores = _compute_category_scores(tests)

    # Compute overall score
    overall = 0.0
    for cat, info in CATEGORIES.items():
        cat_score = category_scores.get(cat, {}).get("score", 0.0)
        overall += cat_score * info["weight"]

    # Per-module pass/fail
    module_results = _compute_module_results(tests)

    return {
        "tests": tests,
        "category_scores": category_scores,
        "module_results": module_results,
        "overall_score": round(overall * 100, 1),
        "total_tests": len(tests),
        "passed": sum(1 for t in tests if t["passed"]),
        "failed": sum(1 for t in tests if not t["passed"]),
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _parse_pytest_stdout(stdout: str) -> list[dict]:
    """Parse pytest -v output to extract test results.

    Handles both Unix (tests/test_foo.py) and Windows (tests\\test_foo.py) paths.
    Lines look like:
      tests/test_foo.py::test_bar PASSED           [ 10%]
      tests\\test_foo.py::test_bar PASSED           [ 10%]
    """
    tests = []
    # Match lines with either / or \ path separators, and optional trailing percentage
    pattern = re.compile(
        r"^(tests[/\\]\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)",
        re.MULTILINE,
    )
    for match in pattern.finditer(stdout):
        nodeid = match.group(1).replace("\\", "/")
        outcome = match.group(2).lower()
        tests.append({
            "nodeid": nodeid,
            "passed": outcome == "passed",
            "outcome": outcome,
            "duration": 0,
        })
    return tests


def _compute_category_scores(tests: list[dict]) -> dict:
    """Compute pass rate for each category based on test-to-category mapping."""
    category_tests: dict[str, list[bool]] = {cat: [] for cat in CATEGORIES}

    for t in tests:
        nodeid = t["nodeid"]
        # Extract module name from nodeid: tests/test_foo.py::test_bar -> test_foo
        module_match = re.search(r"test_(\w+)\.py", nodeid)
        if not module_match:
            continue
        module_key = f"test_{module_match.group(1)}"

        categories = TEST_CATEGORY_MAP.get(module_key, ["correctness"])
        for cat in categories:
            if cat in category_tests:
                category_tests[cat].append(t["passed"])

    scores = {}
    for cat, results in category_tests.items():
        if results:
            passed = sum(1 for r in results if r)
            total = len(results)
            scores[cat] = {
                "score": passed / total,
                "passed": passed,
                "total": total,
                "percentage": round(passed / total * 100, 1),
            }
        else:
            scores[cat] = {"score": 0.0, "passed": 0, "total": 0, "percentage": 0.0}

    return scores


def _compute_module_results(tests: list[dict]) -> dict:
    """Compute per-module pass/fail counts."""
    modules: dict[str, dict] = {}

    for t in tests:
        nodeid = t["nodeid"]
        module_match = re.search(r"(test_\w+)\.py", nodeid)
        if not module_match:
            continue
        module = module_match.group(1)
        if module not in modules:
            modules[module] = {"passed": 0, "failed": 0, "total": 0, "tests": []}
        modules[module]["total"] += 1
        if t["passed"]:
            modules[module]["passed"] += 1
        else:
            modules[module]["failed"] += 1
        modules[module]["tests"].append(t)

    return modules


def generate_html_report(scores: dict, output_path: str) -> str:
    """Generate styled HTML report with scores, progress bars, feature badges."""
    overall = scores["overall_score"]
    if overall > 80:
        badge_color = "#3fb950"
        badge_label = "PASSING"
    elif overall > 60:
        badge_color = "#d29922"
        badge_label = "PARTIAL"
    else:
        badge_color = "#f85149"
        badge_label = "FAILING"

    # Category rows
    cat_rows = ""
    for cat, info in CATEGORIES.items():
        cat_data = scores["category_scores"].get(cat, {})
        pct = cat_data.get("percentage", 0)
        passed = cat_data.get("passed", 0)
        total = cat_data.get("total", 0)
        bar_color = "#3fb950" if pct > 80 else "#d29922" if pct > 60 else "#f85149"

        cat_rows += f"""
        <tr>
          <td><strong>{cat.title()}</strong></td>
          <td>{info['description']}</td>
          <td>{info['weight']:.0%}</td>
          <td>{passed}/{total}</td>
          <td>
            <div class="progress-bar">
              <div class="progress-fill" style="width:{pct}%;background:{bar_color}"></div>
            </div>
            <span class="pct">{pct}%</span>
          </td>
        </tr>"""

    # Module badges
    module_badges = ""
    for module, data in sorted(scores["module_results"].items()):
        if data["failed"] == 0:
            color = "#238636"
            icon = "&#10003;"
        else:
            color = "#da3633"
            icon = "&#10007;"

        module_badges += f"""
        <div class="badge" style="border-color:{color}">
          <span class="badge-icon" style="color:{color}">{icon}</span>
          <span class="badge-name">{module.replace('test_', '')}</span>
          <span class="badge-count">{data['passed']}/{data['total']}</span>
        </div>"""

    # Failed test details
    failed_details = ""
    for t in scores["tests"]:
        if not t["passed"]:
            failed_details += f'<div class="failed-test">&#10007; {t["nodeid"]} [{t["outcome"]}]</div>\n'

    if not failed_details:
        failed_details = '<div class="no-failures">All tests passed!</div>'

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CodeStats Test Report</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; padding: 32px; max-width: 960px; margin: 0 auto; }}
h1 {{ color: #f0f6fc; font-size: 24px; margin-bottom: 8px; }}
h2 {{ color: #f0f6fc; font-size: 18px; margin: 24px 0 12px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
.subtitle {{ color: #8b949e; font-size: 14px; margin-bottom: 24px; }}
.overall {{ display: flex; align-items: center; gap: 16px; margin: 24px 0; padding: 20px; background: #161b22; border: 1px solid #30363d; border-radius: 8px; }}
.overall-score {{ font-size: 48px; font-weight: 700; color: {badge_color}; }}
.overall-badge {{ display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; background: {badge_color}; color: #0d1117; }}
.overall-stats {{ color: #8b949e; font-size: 14px; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
th {{ color: #8b949e; font-weight: 600; }}
.progress-bar {{ display: inline-block; width: 100px; height: 8px; background: #21262d; border-radius: 4px; overflow: hidden; vertical-align: middle; }}
.progress-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
.pct {{ font-size: 12px; margin-left: 6px; color: #8b949e; }}
.badges {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }}
.badge {{ display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; font-size: 13px; }}
.badge-icon {{ font-size: 14px; }}
.badge-name {{ font-weight: 600; color: #f0f6fc; }}
.badge-count {{ color: #8b949e; }}
.failed-test {{ padding: 6px 12px; margin: 4px 0; background: #1c1118; border-left: 3px solid #da3633; font-size: 13px; font-family: monospace; border-radius: 0 4px 4px 0; }}
.no-failures {{ padding: 12px; background: #0b1a12; border-left: 3px solid #238636; border-radius: 0 4px 4px 0; font-size: 14px; }}
footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #21262d; color: #484f58; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<h1>CodeStats Test Report</h1>
<div class="subtitle">Generated {timestamp}</div>

<div class="overall">
  <div>
    <div class="overall-score">{overall}%</div>
    <div class="overall-badge">{badge_label}</div>
  </div>
  <div>
    <div style="font-size:16px;color:#f0f6fc">Overall Score</div>
    <div class="overall-stats">{scores['passed']} passed, {scores['failed']} failed, {scores['total_tests']} total</div>
  </div>
</div>

<h2>Category Breakdown</h2>
<table>
  <thead>
    <tr><th>Category</th><th>Description</th><th>Weight</th><th>Pass/Total</th><th>Score</th></tr>
  </thead>
  <tbody>
    {cat_rows}
  </tbody>
</table>

<h2>Feature Modules</h2>
<div class="badges">
  {module_badges}
</div>

<h2>Failed Tests</h2>
{failed_details}

<footer>
  CodeStats Test Scoring Engine &middot; Built by ClaudeFast
</footer>
</body>
</html>"""

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out.resolve())


if __name__ == "__main__":
    print("Running test suite...")
    scores = run_tests_and_score()

    print(f"\n{'='*60}")
    print(f"  Overall Score: {scores['overall_score']}%")
    print(f"  Passed: {scores['passed']}/{scores['total_tests']}")
    print(f"  Failed: {scores['failed']}/{scores['total_tests']}")
    print(f"{'='*60}")

    for cat, info in CATEGORIES.items():
        cat_data = scores["category_scores"].get(cat, {})
        pct = cat_data.get("percentage", 0)
        print(f"  {cat:15s} ({info['weight']:.0%}) : {pct:5.1f}%")

    print(f"{'='*60}")

    report_path = generate_html_report(scores, ".codestats/test-report.html")
    print(f"\nReport: {report_path}")
