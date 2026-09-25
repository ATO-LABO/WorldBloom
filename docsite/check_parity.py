"""Fail if ja/ and en/ don't have the same set of .md files (relative paths)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def md_files(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*.md")}


def main() -> int:
    ja = md_files(ROOT / "ja")
    en = md_files(ROOT / "en")
    missing_en = ja - en
    missing_ja = en - ja
    if missing_en or missing_ja:
        if missing_en:
            print("Missing in en/:", ", ".join(sorted(missing_en)))
        if missing_ja:
            print("Missing in ja/:", ", ".join(sorted(missing_ja)))
        return 1
    print(f"OK: {len(ja)} pages match in ja/ and en/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
