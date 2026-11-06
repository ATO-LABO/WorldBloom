"""Check saved-world setting edits with isolated copies of the supported world."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import unittest

from execution import world_editor
from execution.library import LibraryStore
from execution.provenance import ConfigError
from viewer import server, world_advanced, world_prototype


ROOT = Path(__file__).resolve().parents[1]
WORLD = "momotaro_plus3_new"


class WorldConditionSettingsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        for name in (f"projects/{WORLD}", "templates/momotaro_plus3", "templates/basic"):
            shutil.copytree(ROOT / name, self.repo / name)
        self.store = LibraryStore(self.repo)

    def state(self):
        return world_editor.snapshot(self.store, WORLD)

    def edit(self, operation, values, target=None, revision=None):
        return world_editor.save(self.store, WORLD, {
            "revision": revision or self.state()["revision"], "operation": operation,
            "target": target, "values": values,
        })

    def test_currency_and_person_balance_save_without_replacing_assets(self):
        initial = self.state()
        currency = initial["economy_state"]["currency"]
        person = initial["people"][0]
        updated = self.edit("economy-settings", {"changes": [{
            "operation": "economy-currency", "target": None,
            "values": {"preset": "USD", "name": "ignored", "unit": "ignored", "asset": currency["asset"]},
        }]})
        self.assertEqual(updated["economy_state"]["currency"]["preset"], "USD")
        self.assertEqual(updated["economy_state"]["currency"]["asset"], currency["asset"])
        self.assertEqual(updated["people"], initial["people"])
        saved = self.edit("person-balance", {"amount": 19}, person["id"])
        updated_person = next(value for value in saved["people"] if value["id"] == person["id"])
        expected = deepcopy(person)
        expected.setdefault("inventory", {})[currency["asset"]] = 19
        self.assertEqual(updated_person, expected)
        with self.assertRaises(ConfigError) as caught:
            self.edit("person-balance", {"amount": 20}, person["id"], initial["revision"])
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(self.state()["people"], saved["people"])

    def test_stamina_world_person_action_and_zero_settings_round_trip(self):
        initial = self.state()
        person_id = initial["people"][0]["id"]
        default = self.edit("stamina-settings", {
            "default_max": 16, "default_recover_per_slot": 2, "exhausted_ratio": 0.2,
            "default_action_cost": 1, "passive_recovery": False,
        })
        self.assertEqual(default["world"]["stamina"]["default_max"], 16)
        person = self.edit("person-stamina", {"max": 12, "current": 8, "recover_per_slot": 0}, person_id)
        self.assertEqual(person["person_stamina"][person_id]["recover_per_slot"], 0)
        self.assertEqual(person["person_stamina"][person_id]["current"], 8)
        action = self.edit("action-stamina", {"adjustment": 2}, "move")
        self.assertEqual(action["world"]["stamina"]["action_cost_adjustments"]["move"], 2)
        zero = self.edit("stamina-zero", {"zero_behavior": "rest"})
        self.assertEqual(zero["world"]["stamina"]["zero_behavior"], "rest")
        self.assertEqual(zero["world"]["action_usage"], initial["world"]["action_usage"])

    def test_production_pages_load_settings_assets(self):
        entry = next(row for row in self.store.worlds() if row["id"] == WORLD)
        snap = self.state()
        prototype = world_prototype.render(entry, snap["world"], snap["people"], revision=snap["revision"])
        advanced = world_advanced.render(entry, self.store, None)
        for asset in ("time-basic.js", "time-basic.css", "genre-controls.js", "ui-help.js"):
            self.assertIn(asset, prototype)
            self.assertIn(asset, server.STATIC_FILES)
        self.assertIn("economy-settings.js", advanced)
        self.assertIn("economy-settings.js", server.STATIC_FILES)
