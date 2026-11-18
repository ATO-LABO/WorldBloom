"""Real saved-world rendering/JS/CSS, without a live application or write API.

Only the public bundled Momotaro YAML is copied into a temporary repository.
HTTP serves an explicit in-memory allowlist on 127.0.0.1; all other browser
requests (including POST, external URLs and WebSockets) fail the check.
--fixture-only validates generation and HTTP without launching any browser.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "2e70a9b216b7b2a80a4228a10a928148060e8fc8"
WORLD = "momotaro_plus3_new"
WORLD_PATH = "/worlds/" + WORLD
sys.path.insert(0, str(ROOT))


def fixtures(directory):
    import yaml
    from viewer import data, world_graph, world_prototype

    sample = ROOT / "projects" / WORLD
    target = directory / "projects" / WORLD
    target.mkdir(parents=True)
    shutil.copyfile(sample / "world.yaml", target / "world.yaml")
    shutil.copytree(sample / "subjects", target / "subjects")
    world = yaml.safe_load((target / "world.yaml").read_text(encoding="utf-8"))
    people = world_graph.load_subjects(target)
    assert people and world.get("zones"), "Public sample is incomplete"
    entry = {"id": WORLD, "name": world["name"]}
    store = SimpleNamespace(configs=SimpleNamespace(repo=directory))
    # Production render sees only this disposable repository, never settings.json.
    with patch.object(data, "ROOT", directory):
        editable = world_prototype.render(entry, world, people, job_store=store, revision="browser-fixture")
        readonly = world_prototype.render(entry, world, people)
    resources = {
        WORLD_PATH: ("text/html; charset=utf-8", editable.encode()),
        "/readonly": ("text/html; charset=utf-8", readonly.encode()),
        "/favicon.ico": ("image/x-icon", b""),
    }
    for name in set(re.findall(r'(?:src|href)="(/static/[^"?]+)', editable + readonly)):
        relative = Path(name.removeprefix("/static/"))
        assert len(relative.parts) == 1 and relative.suffix in {".js", ".css"}
        mime = "text/javascript" if relative.suffix == ".js" else "text/css"
        resources[name] = (mime + "; charset=utf-8", (ROOT / "viewer/static" / relative).read_bytes())
    baseline = subprocess.check_output(
        ["git", "show", BASELINE + ":viewer/static/world-prototype.js"], cwd=ROOT
    )
    return resources, baseline


@contextmanager
def serve(resources, requests, violations):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"method": self.command, "path": self.path})
            resource = resources.get(self.path)
            if resource is None:
                violations.append({"method": self.command, "path": self.path})
                self.send_error(403)
                return
            mime, body = resource
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def deny(self):
            violations.append({"method": self.command, "path": self.path})
            self.send_error(405)

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = deny

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def check_layout(page, expect):
    # Internal horizontal navigation scrolling is allowed; document overflow is not.
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Document overflows horizontally"
    content = page.locator("#wp-content")
    assert content.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), "Story content overflows horizontally"
    action = page.locator(".wp-footer [data-next]")
    expect(action).to_be_visible()
    action.scroll_into_view_if_needed()
    assert action.evaluate("""e => { const r=e.getBoundingClientRect();
        return r.left >= -1 && r.right <= innerWidth+1 && r.top >= 0 && r.bottom <= innerHeight+1;
    }"""), "Bottom action cannot be reached within viewport"
    action.focus()
    expect(action).to_be_focused()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(action).to_be_focused()
    assert action.evaluate("""e => {const s=getComputedStyle(e);
        return e.matches(':focus-visible') && s.outlineStyle !== 'none' && parseFloat(s.outlineWidth)>0;}
    """), "Keyboard focus has no focus-visible outline"


def browser_checks(resources, baseline, origin, output, report):
    from playwright.sync_api import sync_playwright, expect

    script_path = "/static/world-prototype.js"
    fixed = resources[script_path]
    with sync_playwright() as playwright:
        # Use the official runner-provided Chrome and its supported sandbox.
        # Missing Chrome or unavailable sandbox is a failure, never a fallback.
        report["browser_channel"] = "chrome"
        report["sandbox"] = True
        browser = playwright.chromium.launch(channel="chrome", chromium_sandbox=True)
        report["browser_version"] = browser.version
        report["japanese_font"] = subprocess.check_output(
            ["fc-match", "--format=%{family}", "Noto Sans CJK JP"], text=True
        ).strip()
        assert "Noto Sans CJK JP" in report["japanese_font"], "Japanese screenshot font is unavailable"

        def context(width):
            ctx = browser.new_context(viewport={"width": width, "height": 900},
                                      locale="ja-JP", service_workers="block")
            def route(request_route):
                request = request_route.request
                url = urlsplit(request.url)
                if request.method == "GET" and url.scheme == "http" and (url.scheme + "://" + url.netloc) == origin and url.path in resources and not url.query:
                    request_route.continue_()
                else:
                    report["network_violations"].append({"method": request.method, "url": request.url})
                    request_route.abort("blockedbyclient")
            ctx.route("**/*", route)
            def websocket(ws):
                report["network_violations"].append({"method": "WEBSOCKET", "url": ws.url})
                ws.close()
            ctx.route_web_socket("**/*", websocket)
            ctx.on("page", lambda page: page.on("pageerror", lambda error: report["page_errors"].append(str(error))))
            return ctx

        def story(page, screen="description"):
            expect(page.locator("#wp-content")).to_have_attribute("data-screen", screen)
            expect(page.locator("#wp-content h1")).to_have_text("初期物語")
            expect(page.locator('.wp-nav [aria-current="page"]')).to_have_count(1)
            expect(page.locator('.wp-nav [aria-current="page"]')).to_have_attribute("data-screen", "description")
            expect(page).to_have_url(re.compile(r"#screen=" + screen + "$"))

        # Baseline MUST render successfully and exhibit the specific mismatch.
        # Launch/dependency/JS errors never count as reproduction.
        resources[script_path] = (fixed[0], baseline)
        ctx = context(1180)
        page = ctx.new_page()
        try:
            page.goto(origin + WORLD_PATH + "#screen=overview")
            heading = page.locator("#wp-content h1")
            overview = heading.inner_text()
            assert overview != "初期物語"
            page.locator('.wp-nav [data-screen="description"]').click()
            expect(page).to_have_url(re.compile(r"#screen=description$"))
            expect(page.locator("#wp-content")).to_have_attribute("data-screen", "description")
            expect(heading).to_have_text(overview)
            expect(page.locator('[data-view="story:intro"]')).to_have_count(0)
            page.screenshot(path=str(output / "baseline-menu-mismatch.png"), full_page=True)
            page.reload()
            expect(page.locator("#wp-content")).to_have_attribute("data-screen", "overview")
            expect(heading).to_have_text(overview)
            page.goto(origin + WORLD_PATH + "#screen=story")
            expect(heading).to_have_text("初期物語")
            expect(page.locator('.wp-nav [aria-current="page"]')).to_have_count(1)
            expect(page.locator('.wp-nav [aria-current="page"]')).to_have_attribute("data-screen", "overview")
            assert not report["page_errors"] and not report["network_violations"]
            report["baseline"] = {"status": "reproduced", "checks": ["menu falls back to overview", "description hash restores overview", "legacy story renders with incorrect overview menu selection"]}
        finally:
            ctx.close()
            resources[script_path] = fixed

        for width in (1180, 390, 320):
            ctx = context(width)
            page = ctx.new_page()
            result = {"width": width, "status": "running", "checks": []}
            report["fixed"].append(result)
            try:
                page.goto(origin + WORLD_PATH + "#screen=overview")
                menu = page.locator('.wp-nav [data-screen="description"]')
                for _ in range(2):
                    menu.click()
                    story(page)
                    expect(page.locator("#wp-content h1")).to_be_focused()
                result["checks"].append("menu, repeated click, heading focus, aria-current")
                page.locator('[data-view="story:state"]').first.click()
                expect(page.locator(".wp-state")).to_be_visible()
                expect(page.locator('[data-view="story:state"]').first).to_be_focused()
                check_layout(page, expect)
                page.screenshot(path=str(output / f"fixed-{width}-state.png"), full_page=True)
                page.locator('.wp-nav [data-screen="overview"]').click()
                page.go_back()
                story(page)
                expect(page.locator(".wp-state")).to_be_visible()
                page.go_forward()
                expect(page.locator("#wp-content")).to_have_attribute("data-screen", "overview")
                page.go_back()
                story(page)
                page.locator('[data-view="story:intro"]').click()
                expect(page.locator(".wp-story-copy")).to_be_visible()
                check_layout(page, expect)
                page.screenshot(path=str(output / f"fixed-{width}-intro.png"), full_page=True)
                result["checks"].append("intro/state, Back/Forward, retained tab, responsive layout, bottom action, keyboard focus")
                edit = page.locator('[data-edit="intro"]')
                edit.click()
                dialog = page.locator("dialog.wp-dialog")
                field = dialog.locator('[name="text"]')
                original = field.input_value()
                field.fill("Unsaved browser regression draft")
                page.wait_for_timeout(1200)
                assert not report["network_violations"], "Editing attempted an autosave or API request"
                page.once("dialog", lambda prompt: prompt.dismiss())
                dialog.get_by_role("button", name="キャンセル", exact=True).click()
                expect(dialog).to_be_visible()
                expect(field).to_have_value("Unsaved browser regression draft")
                page.screenshot(path=str(output / f"fixed-{width}-cancel-keeps-draft.png"), full_page=True)
                page.once("dialog", lambda prompt: prompt.accept())
                dialog.get_by_role("button", name="キャンセル", exact=True).click()
                expect(dialog).not_to_be_visible()
                expect(edit).to_be_focused()
                edit.click()
                expect(field).to_have_value(original)
                page.keyboard.press("Escape")
                expect(dialog).not_to_be_visible()
                expect(edit).to_be_focused()
                result["checks"].append("Cancel declines discard, discard confirmed, original value restored, Escape, opener focus, no API requests")
                for screen in ("description", "story"):
                    page.goto(origin + WORLD_PATH + "#screen=" + screen)
                    story(page, screen)
                    expect(page.locator('[data-edit="intro"]')).to_be_visible()
                    page.goto(origin + "/readonly#screen=" + screen)
                    story(page, screen)
                    expect(page.locator('[data-edit="intro"]')).to_have_count(0)
                result["checks"].append("description and legacy story direct links, editable/read-only")
                # Give delayed/autosave effects an opportunity to fire; any API
                # request is blocked and recorded, not fulfilled with a fake OK.
                page.wait_for_timeout(500)
                assert not report["page_errors"], report["page_errors"]
                assert not report["network_violations"], report["network_violations"]
                result["status"] = "passed"
            except Exception:
                result["status"] = "failed"
                page.screenshot(path=str(output / f"failure-{width}.png"), full_page=True)
                raise
            finally:
                ctx.close()
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "baseline_commit": BASELINE, "baseline": {"status": "not_run"},
              "fixed": [], "network_violations": [], "page_errors": [], "server_requests": []}
    try:
        with tempfile.TemporaryDirectory(prefix="worldbloom-browser-") as temp:
            resources, baseline = fixtures(Path(temp))
            snapshot = {str(p.relative_to(temp)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in Path(temp).rglob("*") if p.is_file()}
            report["tested_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            report["js_sha256"] = {"baseline": hashlib.sha256(baseline).hexdigest(),
                                   "fixed": hashlib.sha256(resources["/static/world-prototype.js"][1]).hexdigest()}
            with serve(resources, report["server_requests"], report["network_violations"]) as origin:
                if args.fixture_only:
                    from urllib.request import urlopen
                    for path, (_, expected) in resources.items():
                        with urlopen(origin + path, timeout=5) as response:
                            assert response.read() == expected
                    report["status"] = "fixture_only_passed_browser_not_run"
                else:
                    browser_checks(resources, baseline, origin, args.output, report)
                    assert not report["network_violations"] and not report["page_errors"]
                    assert all(r["method"] == "GET" for r in report["server_requests"])
                    after = {str(p.relative_to(temp)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in Path(temp).rglob("*") if p.is_file()}
                    assert after == snapshot, "Disposable sample changed unexpectedly"
                    report["sample_unchanged"] = True
                    report["post_count"] = sum(r["method"] == "POST" for r in report["server_requests"])
                    report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "baseline": report["baseline"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
