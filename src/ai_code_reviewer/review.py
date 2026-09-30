"""PR diff reviewer with offline heuristics and optional LLM mode."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "nit": 3}


@dataclass
class Finding:
    severity: str
    title: str
    file: str
    line: int | None
    rationale: str
    suggestion: str = ""


@dataclass
class ReviewResult:
    summary: str
    findings: list[Finding] = field(default_factory=list)
    provider: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "provider": self.provider,
            "findings": [f.__dict__ for f in self.findings],
        }


def parse_diff(diff_text: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    new_line = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current = {"path": path, "added": [], "removed": []}
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_line = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["added"].append({"line": new_line, "text": line[1:]})
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            current["removed"].append(line[1:])
        else:
            new_line += 1
    return files


def heuristic_review(diff_text: str) -> ReviewResult:
    findings: list[Finding] = []
    for f in parse_diff(diff_text):
        path = f["path"]
        for row in f["added"]:
            text = row["text"]
            ln = row["line"]
            if re.search(r"TODO|FIXME", text):
                findings.append(Finding("minor", "Leftover TODO/FIXME", path, ln, text.strip(), "Resolve or track in an issue."))
            if re.search(r"(?i)password\s*=\s*['\"][^'\"]+['\"]", text):
                findings.append(Finding("critical", "Hardcoded password", path, ln, text.strip(), "Use env/secret manager."))
            if re.search(r"except\s*:\s*$|except Exception:\s*pass", text):
                findings.append(Finding("major", "Bare/swallowed exception", path, ln, text.strip(), "Catch specific errors and log."))
            if "console.log(" in text or re.search(r"\bprint\(", text):
                findings.append(Finding("nit", "Debug logging left in", path, ln, text.strip(), "Remove or use structured logger."))
            if re.search(r"sk-[A-Za-z0-9]{10,}", text):
                findings.append(Finding("critical", "Possible API key in diff", path, ln, "Secret-looking token", "Rotate and remove."))
            if re.search(r"time\.Sleep\(|sleep\(", text) and "test" not in path:
                findings.append(Finding("minor", "Sleep in production path", path, ln, text.strip(), "Prefer backoff/events."))
    summary = (
        f"Found {len(findings)} issue(s)."
        if findings
        else "No heuristic issues found. LGTM with light review."
    )
    return ReviewResult(summary=summary, findings=findings, provider="heuristic")


def llm_review(diff_text: str, api_key: str, model: str) -> ReviewResult | None:
    """Optional OpenAI-compatible review. Falls back to None on failure."""
    prompt = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior code reviewer. Return JSON only with keys "
                    "summary (string) and findings (array of "
                    "{severity,title,file,line,rationale,suggestion}). "
                    "severity in critical|major|minor|nit."
                ),
            },
            {"role": "user", "content": "Review this diff:\n\n" + diff_text[:120000]},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1") + "/chat/completions",
        data=json.dumps(prompt).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
        content = payload["choices"][0]["message"]["content"]
        data = json.loads(content)
        findings = [Finding(**{**f, "line": f.get("line")}) for f in data.get("findings", [])]
        return ReviewResult(summary=data.get("summary", ""), findings=findings, provider="llm")
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TypeError):
        return None


def render_markdown(result: ReviewResult) -> str:
    lines = [f"## AI Code Review ({result.provider})", "", result.summary, ""]
    if not result.findings:
        lines.append("_No findings._")
        return "\n".join(lines)
    for f in sorted(result.findings, key=lambda x: SEVERITY_ORDER.get(x.severity, 9)):
        loc = f"{f.file}:{f.line}" if f.line else f.file
        lines.append(f"- **[{f.severity}]** {f.title} (`{loc}`)")
        lines.append(f"  - {f.rationale}")
        if f.suggestion:
            lines.append(f"  - Suggestion: {f.suggestion}")
    return "\n".join(lines)


def evaluate(fixture_dir: Path) -> dict[str, Any]:
    """Compare heuristic findings against expected.json fixtures."""
    results = []
    for case in sorted(fixture_dir.iterdir()):
        if not case.is_dir():
            continue
        diff = (case / "diff.patch").read_text(encoding="utf-8")
        expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
        got = heuristic_review(diff)
        got_titles = {f.title for f in got.findings}
        expected_titles = set(expected.get("must_find", []))
        missing = sorted(expected_titles - got_titles)
        unexpected_critical = [
            f.title for f in got.findings if f.severity == "critical" and f.title not in expected_titles and expected.get("forbid_extra_critical")
        ]
        ok = not missing and not unexpected_critical
        results.append({"case": case.name, "ok": ok, "missing": missing, "findings": [f.title for f in got.findings]})
    passed = sum(1 for r in results if r["ok"])
    return {"passed": passed, "total": len(results), "results": results}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="AI code reviewer")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review")
    r.add_argument("--diff-file", type=Path, required=True)
    r.add_argument("--format", choices=["json", "markdown"], default="markdown")
    r.add_argument("--llm", action="store_true", help="try LLM if OPENAI_API_KEY set")

    e = sub.add_parser("eval")
    e.add_argument("--fixtures", type=Path, default=Path("evals/fixtures"))

    args = p.parse_args(argv)

    if args.cmd == "eval":
        report = evaluate(args.fixtures)
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] == report["total"] else 1

    diff_text = args.diff_file.read_text(encoding="utf-8")
    result = heuristic_review(diff_text)
    if args.llm:
        key = os.getenv("OPENAI_API_KEY", "")
        if key:
            llm = llm_review(diff_text, key, os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
            if llm is not None:
                result = llm

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
