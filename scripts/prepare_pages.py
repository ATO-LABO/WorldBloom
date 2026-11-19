"""Prepare a NEW, unpublished Pages staging tree; never deploy or erase a tree.

Input is a clean snapshot of the current gh-pages tree (without .git).
Keep docs and existing public assets byte-for-byte, move the old index to
viewer/index.html, and retain legacy stories URLs with their list back-link
pointing to viewer/. No generation, network, git push or deletion is performed.
"""
from __future__ import annotations
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.publication_guard import PublicationError, check_name, check_content, ensure_output_safe, validate_files

SAMPLE = "stories/momotaro-I-low.html"
BACK = 'href="../index.html"'
NEW_BACK = 'href="../viewer/"'

class Synopsis(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.found = False
        self.done = False
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if self.done: return
        if tag == "section":
            self.depth += 1
            self.found = True
    def handle_endtag(self, tag):
        if tag == "section" and self.depth:
            self.depth -= 1
            if not self.depth: self.done = True
    def handle_data(self, data):
        if self.depth: self.parts.append(data)

def synopsis_text(page):
    parser = Synopsis()
    parser.feed(page)
    text = "".join(parser.parts).strip()
    if not parser.done or not text:
        raise PublicationError("Missing public sample synopsis")
    return text

class IndexLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.invalid = False
    def handle_starttag(self, tag, attrs):
        if tag == "base": self.invalid = True
        for key, value in attrs:
            if key in ("src", "srcset"):
                self.invalid = True  # unexpected asset path needs explicit review
            if key == "href" and value:
                self.hrefs.append(value)

def viewer_index(page):
    links = IndexLinks(); links.feed(page)
    if links.invalid:
        raise PublicationError("Unexpected viewer asset or base element")
    result = page
    for href in set(links.hrefs):
        url = urlsplit(href)
        if url.scheme or url.netloc or href.startswith("#"):
            continue
        if not href.startswith("stories/") or url.query or url.fragment or ".." in Path(href).parts:
            raise PublicationError("Unexpected viewer relative link")
        old = 'href="' + href + '"'
        if old not in page:
            raise PublicationError("Unexpected viewer link serialization")
        result = result.replace(old, 'href="../' + href + '"')
    return result

def manifest(files):
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}

def prepare(published: Path, output: Path, template_root: Path | None = None):
    published = Path(published)
    output = Path(output)
    template_root = template_root or ROOT / "site"
    ensure_output_safe(output, [published, ROOT, template_root])
    if output.exists() or output.is_symlink():
        raise PublicationError("Output must not exist; no existing directory is ever erased")
    if published.is_symlink() or not published.is_dir():
        raise PublicationError("Input must be a plain published snapshot directory")
    paths = sorted(published.rglob("*"))
    validate_files(paths, published)
    original = {p.relative_to(published).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    if not all(name in original for name in ("index.html", SAMPLE, "docs/index.html")):
        raise PublicationError("Incomplete published snapshot")
    if any(name.startswith("viewer/") for name in original) or "assets/signboard.css" in original:
        raise PublicationError("Existing signboard/viewer must be reconciled, not overwritten")
    index = original["index.html"].decode("utf-8")
    story = original[SAMPLE].decode("utf-8")
    template = (template_root / "index.html").read_text(encoding="utf-8")
    if template.count("{{SYNOPSIS}}") != 1:
        raise PublicationError("Unexpected template marker")
    files = dict(original)
    files["index.html"] = template.replace("{{SYNOPSIS}}", html.escape(synopsis_text(story))).encode("utf-8")
    files["viewer/index.html"] = viewer_index(index).encode("utf-8")
    files["assets/signboard.css"] = (template_root / "signboard.css").read_bytes()
    changed_stories = []
    for name, data in original.items():
        if name.startswith("stories/") and name.endswith(".html"):
            text = data.decode("utf-8")
            if text.count(BACK) != 1:
                raise PublicationError("Unexpected legacy story back-link: " + name)
            files[name] = text.replace(BACK, NEW_BACK, 1).encode("utf-8")
            changed_stories.append(name)
    for name, data in files.items():
        check_name(name); check_content(name, data)
    # All input bytes were validated and every planned change prepared before writes.
    # mkdir is exclusive, including on a racing process. No cleanup/deletion on error.
    output.mkdir(parents=False, exist_ok=False)
    for name, data in sorted(files.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as f: f.write(data)
    actual = {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    if manifest(actual) != manifest(files):
        raise PublicationError("Staging read-back mismatch; leave evidence untouched")
    if any((published / name).read_bytes() != data for name, data in original.items()):
        raise PublicationError("Published snapshot changed during preparation")
    return {"status": "prepared-not-published", "input": manifest(original), "output": manifest(files),
            "changed": ["index.html", *changed_stories], "added": ["assets/signboard.css", "viewer/index.html"],
            "docs_unchanged": all(files[n] == d for n, d in original.items() if n.startswith("docs/"))}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.published, args.out), ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
