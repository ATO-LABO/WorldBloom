"""Documentation freshness checks, run in CI and locally.

Exit 1 (build-breaking):
  (a) ja/ and en/ don't have the same set of .md files.
  (d) CLI help snippets (docsite/snippets/cli/*.txt) are stale.

Warnings only (printed, does not affect exit code):
  (b) an en/ page's `ja_rev` front matter doesn't match its ja/ source's
      current body_rev (sha1 of the page minus front matter, so editing
      reviewed:/sources: doesn't flag translations) -- or is missing
      entirely (untranslated stub).
  (c) a ja/ page's `sources:` files changed since its `reviewed:` commit --
      or it has no `reviewed:` at all.

`python docsite/check.py --ja-rev ja/<path>.md` prints the value to put in
an en page's ja_rev after translating it.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCSITE = Path(__file__).resolve().parent


def md_files(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*.md")}


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    data = yaml.safe_load(text[4:end])
    return data if isinstance(data, dict) else {}


def body_rev(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[text.find("\n", end + 4) + 1:]
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def check_parity() -> bool:
    """(a) ja/ and en/ have the same set of .md files."""
    ja = md_files(DOCSITE / "ja")
    en = md_files(DOCSITE / "en")
    missing_en = ja - en
    missing_ja = en - ja
    if not missing_en and not missing_ja:
        print(f"OK: {len(ja)} pages match in ja/ and en/")
        return True
    if missing_en:
        print("Missing in en/:", ", ".join(sorted(missing_en)))
    if missing_ja:
        print("Missing in ja/:", ", ".join(sorted(missing_ja)))
    return False


def check_en_translations() -> None:
    """(b) en/<page>.md's ja_rev vs. the current ja/<page>.md body_rev."""
    stale = []
    for en_path in sorted((DOCSITE / "en").rglob("*.md")):
        rel = en_path.relative_to(DOCSITE / "en").as_posix()
        ja_path = DOCSITE / "ja" / rel
        if not ja_path.exists():
            continue
        current = body_rev(ja_path)
        ja_rev = str(front_matter(en_path).get("ja_rev") or "")
        if not ja_rev:
            stale.append(f"en/{rel}: ja_rev が未設定（未翻訳スタブの可能性）")
        elif ja_rev != current:
            stale.append(f"en/{rel}: ja_rev={ja_rev} が ja/{rel} の現在値={current} と不一致")
    if stale:
        print("\n要追従/未翻訳 (warning only):")
        for line in stale:
            print(f"  - {line}")


def check_ja_sources() -> None:
    """(c) ja/<page>.md's sources: vs. its reviewed: commit."""
    stale = []
    for ja_path in sorted((DOCSITE / "ja").rglob("*.md")):
        rel = ja_path.relative_to(DOCSITE / "ja").as_posix()
        meta = front_matter(ja_path)
        sources = meta.get("sources")
        if not sources:
            continue
        if not isinstance(sources, list):
            stale.append(f"ja/{rel}: sources がリストではありません（型={type(sources).__name__}）")
            continue
        reviewed = str(meta.get("reviewed") or "")
        if not reviewed:
            stale.append(f"ja/{rel}: reviewed が未設定")
            continue
        try:
            log = git("log", "--format=%h", f"{reviewed}..HEAD", "--", *sources)
        except subprocess.CalledProcessError:
            stale.append(f"ja/{rel}: reviewed={reviewed[:12]} が解決できません")
            continue
        if log:
            commits = log.splitlines()
            stale.append(f"ja/{rel}: 情報源が変わっています ({len(commits)}件, 例 {commits[0]})")
    if stale:
        print("\n情報源が更新されたページ (warning only):")
        for line in stale:
            print(f"  - {line}")


def check_cli_snippets() -> bool:
    """(d) docsite/snippets/cli/*.txt is up to date."""
    result = subprocess.run(
        [sys.executable, str(DOCSITE / "gen_cli.py"), "--check"], cwd=ROOT,
    )
    return result.returncode == 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if sys.argv[1:2] == ["--ja-rev"]:
        for arg in sys.argv[2:]:
            print(body_rev(DOCSITE / arg), arg)
        return 0
    ok = check_parity()
    check_en_translations()
    check_ja_sources()
    ok = check_cli_snippets() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
