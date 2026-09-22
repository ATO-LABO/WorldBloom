"""World workspace preserves navigation and treats input as data, never markup."""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from execution.library import LibraryStore
from viewer import library_pages, pages, world_graph, world_prototype

ROOT = Path(__file__).resolve().parents[1]


class WorldPrototypeTests(unittest.TestCase):
    def test_existing_worlds_and_phase_navigation_are_preserved(self):
        store = LibraryStore(ROOT)
        for world in store.worlds():
            with self.subTest(world=world['id']):
                source = library_pages._world_yaml_mapping(ROOT, world['id'])
                subjects = world_graph.load_subjects(ROOT / 'projects' / world['id'])
                before = json.dumps([source, subjects], ensure_ascii=False, sort_keys=True)
                pin = {'world': {'id': world['id']}, 'run': 'experiment', 'output_run': 'catalog-id'}
                with patch.object(LibraryStore, 'write', side_effect=AssertionError('preview must not write')):
                    result = world_prototype.render(world, source, subjects, pin=pin)
                original = pages.document('Original', '', world={'id': world['id']}, phase='world', pin=pin)
                nav = lambda text: re.search(r'<nav class="phase-band".*?</nav>', text).group()
                self.assertEqual(nav(result), nav(original))
                self.assertIn('world-prototype.js', result)
                self.assertEqual(before, json.dumps([source, subjects], ensure_ascii=False, sort_keys=True))

    def test_embedded_input_cannot_escape_json_script(self):
        attack = '</script><img src=x onerror=alert(1)>'
        result = world_prototype.render({'id': 'test', 'name': attack}, {}, [{'id': attack}])
        self.assertNotIn(attack, result)
        payload = re.search(r'<script type="application/json" id="wp-data">(.*?)</script>', result).group(1)
        self.assertEqual(json.loads(payload)['people'][0]['id'], attack)

    def test_empty_world_has_readonly_guidance_and_advanced_link(self):
        result = world_prototype.render({'id': 'empty', 'name': 'Empty'}, {}, [])
        self.assertIn('data-world-prototype', result)
        self.assertIn('href="/worlds/empty?view=advanced"', result)
        self.assertIn('閲覧モード', result)
        self.assertNotIn('data-quick-start', result)

    def test_no_expansion_html_omits_the_template_entirely(self):
        # V2: 0 approved and 0 proposed -> library_pages passes expansion_html=""
        # -> no <template id="wp-expansion"> at all (not even an empty one).
        result = world_prototype.render({'id': 'empty', 'name': 'Empty'}, {}, [])
        self.assertNotIn('id="wp-expansion"', result)

    def test_expansion_html_ships_as_an_inert_template_outside_wp_content(self):
        # V2 (viewer review): the "後から生まれたもの" section used to sit as
        # a direct sibling of #wp-content in the .wp 2-column grid, which
        # stranded it in the left column. It now ships as an inert
        # <template id="wp-expansion"> that world-prototype.js's overview
        # screen appends into itself.
        marker = '<p>後から生まれたものの中身</p>'
        result = world_prototype.render({'id': 'empty', 'name': 'Empty'}, {}, [], expansion_html=marker)
        match = re.search(r'<template id="wp-expansion">(.*?)</template>', result, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), marker)
        # A <template>'s content never renders in place -- but assert the
        # placement too: it must come after #wp-content closes, not before
        # (i.e. not nested inside the JS-managed area) and not be duplicated
        # loose in the body outside the template tag.
        content_close = result.index('id="wp-content"></div>')
        template_open = result.index('<template id="wp-expansion">')
        self.assertLess(content_close, template_open)
        self.assertEqual(result.count(marker), 1)


if __name__ == '__main__':
    unittest.main()
