"""Check the server-controlled values behind the reported DOM XSS paths."""
from html.parser import HTMLParser
import json
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit

from viewer import screening_pages, sifting_pages, world_prototype


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.options = []
        self.navigation = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "select":
            self.navigation = "data-sc-navigate" in attrs or "data-sf-run" in attrs
        if tag == "option" and self.navigation:
            self.options.append(attrs["value"])

    def handle_endtag(self, tag):
        if tag == "select":
            self.navigation = False


class HtmlBoundaryTests(unittest.TestCase):
    ATTACK = '\"><img src=x onerror="alert(1)"><script>alert(1)</script>'

    def assert_safe_options(self, body):
        parser = Elements(body)
        elements, options = parser.elements, parser.options
        self.assertTrue(options)
        for value in options:
            parsed = urlsplit(value)
            self.assertEqual((parsed.scheme, parsed.netloc), ("", ""))
            self.assertTrue(parsed.path.startswith("/"))
            self.assertFalse(parsed.path.startswith("//"))
        self.assertFalse(any(tag == "img" or "onerror" in attrs for tag, attrs in elements))

    def test_screening_picker_encodes_untrusted_run_names(self):
        bodies = []
        handler = SimpleNamespace(server=SimpleNamespace(job_store=None), _send_html=bodies.append)
        screening_pages.shell(handler, "Fixture", "", query={}, history=[{
            "run_id": "javascript:alert(1)" + self.ATTACK,
            "label": self.ATTACK, "experiment_name": self.ATTACK,
        }])
        self.assert_safe_options(bodies[0])

    def test_sifting_picker_encodes_untrusted_ids_in_all_modes(self):
        history = [{"run_id": "javascript:alert(1)" + self.ATTACK,
                    "experiment_name": "//example.invalid/" + self.ATTACK,
                    "label": self.ATTACK, "publication_revision": 1}]
        for mode in ("tray", "grid", "run"):
            with self.subTest(mode=mode):
                self.assert_safe_options(sifting_pages.picker({"history": history, "run_id": "unused"}, mode))

    def test_world_config_url_has_server_owned_prefix_and_json_stays_inert(self):
        world = {"id": "javascript:alert(1)" + self.ATTACK, "name": "Fixture", "genre": self.ATTACK}
        body = world_prototype.render(world, {"scheduled_events": [{
            "id": self.ATTACK, "label": self.ATTACK, "__index": self.ATTACK,
        }]}, [])
        parser = Elements(body)
        self.assertFalse(any(tag == "img" for tag, _ in parser.elements))
        start = body.index('id="wp-data">') + len('id="wp-data">')
        model = json.loads(body[start:body.index('</script>', start)])
        self.assertTrue(model["configUrl"].startswith("/configs/new?project="))
        self.assertEqual(urlsplit(model["configUrl"]).scheme, "")
        self.assertEqual(model["world"]["scheduled_events"][0]["label"], self.ATTACK)
