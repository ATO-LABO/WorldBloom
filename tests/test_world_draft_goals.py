"""General objectives: real engine predicates and adoption, no model calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import yaml

import test_world_drafts as legacy_tests
from execution.provenance import ConfigError
from execution.world_drafts import compile_draft, mock_response, parse_response, validate, existing_provider
from gapengine.route_paths import _load_world_and_subjects
from gapengine.knowledge_text import _goal_line
from viewer.world_graph import world_summary


class GeneralGoalTests(unittest.TestCase):
    setUp = legacy_tests.DraftTests.setUp

    def draft(self, conditions, description="概要に沿った目標"):
        d = parse_response(mock_response("新しい世界", "配送を含まない概要"))
        d["people"][0]["goal"] = {"description": description, "conditions": conditions}
        d["people"][1]["goal"] = {"description": "町で暮らす", "conditions": []}
        d["items"] = []
        d["facts"] = []
        return d

    def load(self, d):
        self.store.provider = lambda *_: json.dumps(d, ensure_ascii=False)
        record = self.store.create(self.token, d["name"], d["overview"])
        self.assertEqual(record["state"], "review", record)
        self.store.adopt(self.token, record["revision"])
        project = self.repo / "projects" / record["world_id"]
        world, subjects = _load_world_and_subjects(project, self.repo / "templates/basic")
        world.bind_subjects(subjects)
        return world, subjects

    def attained(self, world, subjects, turn=1):
        actor = subjects[world.protagonist]
        return world.endings[0]["predicate"].evaluate(world.namespace(actor, list(subjects.values()), turn=turn, day=1))

    def test_no_items_needed_for_arrival_and_goal_visible_after_adoption(self):
        d = self.draft([{"type": "reach", "place": "森"}], "未知の森にたどり着く")
        world, subjects = self.load(d)
        self.assertFalse(self.attained(world, subjects))
        subjects["アオ"].zone = "森"
        self.assertTrue(self.attained(world, subjects))
        self.assertEqual(_goal_line(subjects["アオ"]), "目的: 未知の森にたどり着く")
        self.assertIn("未知の森にたどり着く", world_summary([{"id":"アオ", "goal":d["people"][0]["goal"]}], protagonist="アオ", antagonist=None))

    def test_meeting_requires_same_place_and_living_people(self):
        world, subjects = self.load(self.draft([{"type":"meet", "person":"レン"}]))
        self.assertFalse(self.attained(world, subjects))
        subjects["アオ"].zone = "森"
        self.assertTrue(self.attained(world, subjects))
        subjects["レン"].vitality = "dead"
        self.assertFalse(self.attained(world, subjects))

    def test_friendship_requires_both_directions(self):
        world, subjects = self.load(self.draft([{"type":"befriend", "person":"レン", "threshold":0.6}]))
        self.assertFalse(self.attained(world, subjects))
        world.relations.change("アオ", "レン", affinity=0.6)
        self.assertFalse(self.attained(world, subjects))
        world.relations.change("レン", "アオ", affinity=0.6)
        self.assertTrue(self.attained(world, subjects))

    def test_discovery_uses_defined_fact_with_investigation_source(self):
        d = self.draft([{"type":"discover", "fact":"街の秘密"}], "街の秘密を解明する")
        d["facts"] = [{"name":"街の秘密", "description":"鐘は地下の水車で鳴っている", "place":"森"}]
        world, subjects = self.load(d)
        self.assertEqual(world.facts["街の秘密"]["sources"][0]["zone"], "森")
        self.assertFalse(self.attained(world, subjects))
        subjects["アオ"].knowledge.add("街の秘密")
        self.assertTrue(self.attained(world, subjects))

    def test_survival_waits_for_end_of_requested_day_and_actor_alive(self):
        world, subjects = self.load(self.draft([{"type":"survive", "day":7}]))
        self.assertFalse(self.attained(world, subjects, turn=27))
        self.assertTrue(self.attained(world, subjects, turn=28))
        subjects["アオ"].vitality = "dead"
        self.assertFalse(self.attained(world, subjects, turn=28))

    def test_defeat_accepts_incapacitation_without_requiring_death(self):
        world, subjects = self.load(self.draft([{"type":"defeat", "person":"レン"}]))
        self.assertFalse(self.attained(world, subjects))
        subjects["レン"].vitality = "downed"
        self.assertTrue(self.attained(world, subjects))

    def test_acquisition_does_not_add_delivery_and_composite_requires_all(self):
        d = self.draft([{"type":"acquire", "item":"鍵"}, {"type":"reach", "place":"森"}])
        d["items"] = [{"name":"鍵", "holder":"レン"}]
        world, subjects = self.load(d)
        self.assertIsNone(subjects["アオ"].goal.deliver_to)
        subjects["アオ"].inventory["鍵"] = 1
        self.assertFalse(self.attained(world, subjects))
        subjects["アオ"].zone = "森"
        self.assertTrue(self.attained(world, subjects))

    def test_delivery_keeps_existing_possession_at_destination_meaning(self):
        d = self.draft([{"type":"deliver", "item":"鍵", "place":"町"}])
        d["items"] = [{"name":"鍵", "holder":"レン"}]
        world, subjects = self.load(d)
        self.assertFalse(self.attained(world, subjects))
        subjects["アオ"].inventory["鍵"] = 1
        self.assertTrue(self.attained(world, subjects))
        self.assertEqual(subjects["アオ"].goal.deliver_to, "町")

    def test_narrative_goal_is_preserved_without_invented_success(self):
        d = self.draft([], "自分にとっての幸せを見つける")
        proposal, world, texts, summary = validate(self.repo, d)
        self.assertEqual(world["ending"][0]["when"], "False")
        self.assertEqual(world["target_ending"], ["manual_review"])
        self.assertIn("アオ", summary["narrative_only_goals"])
        self.assertEqual(yaml.safe_load(texts["subjects/person_01.yaml"])["goal"]["description"], "自分にとっての幸せを見つける")
        self.load(d)

    def test_invalid_or_executable_conditions_cannot_be_saved(self):
        bad_conditions = [
            {"type":"reach", "place":"未登録"}, {"type":"acquire", "item":"未登録"},
            {"type":"meet", "person":[]}, {"type":"meet", "person":"アオ"},
            {"type":"discover", "fact":"未登録"}, {"type":"survive", "day":True},
            {"type":"survive", "day":8}, {"type":"befriend", "person":"レン", "threshold":float('nan')},
            {"type":"befriend", "person":"レン", "threshold":True},
            {"type":"befriend", "person":"レン", "threshold":0},
            {"type":"reach", "place":"森", "when":"__import__('os')"},
            {"type":"eval", "code":"True"}, {"type":[]}, None,
        ]
        for condition in bad_conditions:
            with self.subTest(condition=condition), self.assertRaises(ConfigError):
                compile_draft(self.draft([condition]))
        self.assertFalse((self.repo / "projects").exists())

    def test_quoted_names_are_data_in_engine_predicates(self):
        d = self.draft([{"type":"reach", "place":"森"}])
        actor = "旅人' or True"
        d["people"][0]["name"] = d["protagonist"] = actor
        world, subjects = self.load(d)
        self.assertFalse(self.attained(world, subjects))
        subjects[actor].zone = "森"
        self.assertTrue(self.attained(world, subjects))

    def test_prompt_uses_overview_without_forcing_delivery(self):
        from execution.generation import Response
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"output":{"default_backend":"ollama", "ollama":{"model":"test"}}}), encoding="utf8")
        d = self.draft([{"type":"reach", "place":"森"}])
        raw = json.dumps({"message":{"content":json.dumps(d)}, "done":True, "done_reason":"stop"}).encode()
        with patch("execution.generation.transport", return_value=Response(raw)) as transport:
            existing_provider(settings)("宇宙の旅", "迷子の探検家が故郷に帰る")
        prompt = transport.call_args.args[0]["prompt"]
        self.assertIn("迷子の探検家が故郷に帰る", prompt)
        self.assertIn('"type":"discover"', prompt)
        self.assertNotIn("主人公の目標は品物を場所へ届ける形", prompt)


if __name__ == "__main__":
    unittest.main()
