"""Reading and editor boundaries for the world workspace."""
from html.parser import HTMLParser
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml

from execution.library import LibraryStore
from viewer import library_pages

ROOT = Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tags = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class WorldEditorialTests(unittest.TestCase):
    def setUp(self):
        self.store = LibraryStore(ROOT)
        self.world = next(w for w in self.store.worlds() if w['id'] == 'momotaro')

    def test_editor_targets_match_subject_ids_and_keep_each_file_once(self):
        before = {rel: self.store.read('world', 'momotaro', rel)
                  for rel in self.store.world_files('momotaro')}
        markup = library_pages.render_world_detail(self.world, self.store, object())
        areas = [a for tag, a in Elements(markup).tags if tag == 'textarea' and 'data-file' in a]
        self.assertEqual(len(areas), len(before))
        self.assertEqual(len({a['data-file'] for a in areas}), len(before))
        for area in areas:
            if area['data-file'].startswith('subjects/'):
                self.assertEqual(area['data-subject-id'], yaml.safe_load(before[area['data-file']])['id'])
        self.assertEqual(before, {rel: self.store.read('world', 'momotaro', rel) for rel in before})

    def test_read_only_world_does_not_offer_save_or_start_requests(self):
        tags = Elements(library_pages.render_world_detail(self.world, self.store, None)).tags
        self.assertFalse(any('data-quick-start' in attrs for _, attrs in tags))
        self.assertFalse(any(attrs.get('data-action') in ('save-file', 'add-subject') for _, attrs in tags))
        self.assertTrue(any('data-world-person' in attrs for _, attrs in tags))

    def test_untrusted_subject_content_cannot_create_markup(self):
        malicious = '<img src=x onerror="alert(1)">'
        subjects = [{'id': malicious, 'identity': {'true': malicious},
                     'goal': {'target': malicious}, 'inventory': {malicious: 1}, 'knowledge': [malicious]}]
        with patch.object(library_pages.world_graph, 'relation_svg', return_value=''), \
             patch.object(library_pages.world_graph, 'character_table', return_value=''), \
             patch.object(library_pages.world_graph, 'character_readout_html', return_value=''):
            markup = library_pages._characters_panel(self.world, {}, subjects, self.store, '')
        self.assertNotIn('<img', markup)
        self.assertIn('&lt;img', markup)

    def test_people_links_reach_existing_articles_and_stat_dialogs(self):
        tags = Elements(library_pages.render_world_detail(self.world, self.store, object())).tags
        ids = [attrs['id'] for _, attrs in tags if 'id' in attrs]
        self.assertEqual(len(ids), len(set(ids)))
        for _, attrs in tags:
            if 'data-world-person' in attrs:
                self.assertIn(attrs['href'][1:], ids)
            if 'data-world-sheet' in attrs:
                self.assertIn(attrs['data-world-sheet'], ids)


if __name__ == '__main__':
    unittest.main()
