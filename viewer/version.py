"""Which code is this viewer running, and is it the latest main on GitHub?

Source of truth: `git log` when the repo has .git; the packaged app/ folder has
none, so the packaging step writes app/VERSION.txt ("<sha> <YYYY-MM-DD>").
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "ATO-LABO/WorldBloom"


def _git(*args):
    try:
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                                encoding="utf-8", timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


@lru_cache(maxsize=1)  # the running code doesn't change until the server restarts
def current():
    """{"sha", "date", "source"}; sha/date are None when nothing tells us."""
    line = _git("log", "-1", "--format=%H %cs")
    source = "git"
    if not line:
        try:
            line = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
            source = "VERSION.txt"
        except OSError:
            line, source = "", None
    sha, _, date = line.partition(" ")
    return {"sha": sha or None, "date": date or None, "source": source}


def label():
    info = current()
    return f"{info['sha'][:7]} · {info['date']}" if info["sha"] else "版不明"


def dirty():
    """True/False in a git checkout, None otherwise (not cached: edits happen live)."""
    status = _git("status", "--porcelain", "--untracked-files=no")
    return None if status is None else status != ""


def compare_with_main(sha):
    """GitHub compare sha...main -> {"status", "behind_by", "ahead_by"} or {"error"}."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/compare/{sha}...main",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "WorldBloom-viewer"})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"error": "このコミットは GitHub にありません（未 push の可能性）"}
        return {"error": str(error)}
    except Exception as error:  # offline, rate limit: report, never 500
        return {"error": str(error)}
    # base=sha, head=main: main's "ahead_by" is how far this copy is behind.
    return {"status": body.get("status"), "behind_by": body.get("ahead_by", 0),
            "ahead_by": body.get("behind_by", 0)}


def render_body(check):
    info = current()
    rows = [("コミット", info["sha"] or "不明"), ("日付", info["date"] or "不明"),
            ("取得元", {"git": "git リポジトリ", "VERSION.txt": "配布フォルダの VERSION.txt"}.get(info["source"], "なし"))]
    changed = dirty() if info["source"] == "git" else None
    if changed:
        rows.append(("作業中の変更", "あり（コミットされていない変更を含むコードで動いています）"))
    table = "<dl>" + "".join(f"<dt>{html.escape(str(k))}</dt><dd>{html.escape(str(v))}</dd>" for k, v in rows) + "</dl>"
    if not info["sha"]:
        result = "<p>版を特定できないため、最新版かどうかは確認できません。</p>"
    elif not check:
        result = '<p><a class="button" href="/version?check=1">GitHub の最新版と比べる</a></p>'
    else:
        diff = compare_with_main(info["sha"])
        if "error" in diff:
            result = f'<p role="alert">確認できませんでした（{html.escape(str(diff["error"]))}）。</p>'
        elif diff["status"] == "identical":
            result = "<p><strong>最新版です。</strong>GitHub の main と同じコミットです。</p>"
        elif diff["behind_by"]:
            result = (f'<p><strong>最新版ではありません。</strong>GitHub の main に {diff["behind_by"]} 件の新しいコミットがあります。</p>'
                      f'<p><a href="https://github.com/{REPO}/compare/{info["sha"]}...main" target="_blank" rel="noopener">差分を GitHub で見る</a></p>')
        else:
            result = f'<p>GitHub の main より {diff["ahead_by"]} 件新しいコミットで動いています（未公開の変更）。</p>'
        result += '<p><a href="/version?check=1">もう一度確認</a></p>'
    return (f'<section class="card">{table}{result}'
            f'<p class="muted">配布版の入手先: <a href="https://github.com/{REPO}/releases" target="_blank" rel="noopener">GitHub Releases</a></p></section>')
