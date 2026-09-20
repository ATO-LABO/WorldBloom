"""WB-UI-025: the 行動図鑑 catalog stays honest about what the engine and

each world's genre actually do -- it must never invent a subtype, a
placeholder value, or an "active" verb that isn't backed by real code or
real world data.
"""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from engine.world import World
from viewer import action_catalog

ROOT = Path(__file__).resolve().parents[1]

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _world(project_id, genre_id):
    return World.from_yaml(
        ROOT / "projects" / project_id / "world.yaml",
        action_graph_path=ROOT / "templates" / genre_id / "action_graph.yaml",
    )


class ActionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = action_catalog._load_catalog()
        self.momotaro = _world("momotaro", "momotaro")
        self.romance = _world("romance", "romance")
        self.detective = _world("detective", "detective")

    # ------------------------------------------------------------ coverage

    def test_every_implemented_verb_is_catalogued(self):
        engine_verbs = action_catalog._engine_verb_methods()
        catalog_verbs = set(self.catalog["verbs"])
        self.assertEqual(engine_verbs - catalog_verbs, set())

    def test_only_the_two_known_gaps_are_marked_unimplemented(self):
        engine_verbs = action_catalog._engine_verb_methods()
        catalog_verbs = set(self.catalog["verbs"])
        self.assertEqual(catalog_verbs - engine_verbs, {"steal_credit", "pursue_target"})

    # --------------------------------------------------------- placeholders

    def test_all_placeholders_resolve_against_a_real_world(self):
        facts = action_catalog.world_facts(self.momotaro)
        used = set()
        for entry in self.catalog["verbs"].values():
            for kind in entry.get("kinds") or []:
                for field in ("precondition", "target", "effect"):
                    used.update(_PLACEHOLDER.findall(str(kind.get(field, ""))))
        self.assertEqual(used - set(facts), set())
        # ...and no dead fact nobody's catalog text references either.
        self.assertEqual(set(facts) - used, set())

    # ------------------------------------------------------------- status

    def test_momotaro_fight_is_active_and_steal_credit_is_unimplemented(self):
        union, _ = action_catalog._subject_verb_index(ROOT, "momotaro")
        self.assertEqual(action_catalog._verb_status("fight", self.momotaro, union), "active")
        self.assertEqual(
            action_catalog._verb_status("steal_credit", self.momotaro, union), "unimplemented",
        )

    def test_romance_prunes_fight_and_leaves_rethink_unused(self):
        union, _ = action_catalog._subject_verb_index(ROOT, "romance")
        self.assertEqual(action_catalog._verb_status("fight", self.romance, union), "pruned")
        self.assertEqual(action_catalog._verb_status("rethink", self.romance, union), "unused")

    def test_detective_activates_rethink(self):
        union, _ = action_catalog._subject_verb_index(ROOT, "detective")
        self.assertEqual(action_catalog._verb_status("rethink", self.detective, union), "active")

    # -------------------------------------------------------- node lookup

    def test_investigate_kinds_resolve_to_this_genres_subtype_names(self):
        gather_node = action_catalog._node_for(self.momotaro, "investigate", "gather")
        scout_node = action_catalog._node_for(self.momotaro, "investigate", None)
        self.assertEqual(gather_node["subtype"], "gather")
        self.assertEqual(scout_node["subtype"], "scout")

        romance_scout_node = action_catalog._node_for(self.romance, "investigate", None)
        self.assertEqual(romance_scout_node["subtype"], "understand_history")
        # romance's action_graph has no `when: gather` node for investigate.
        self.assertIsNone(action_catalog._node_for(self.romance, "investigate", "gather"))

    # ------------------------------------------------------------- render

    def test_panel_renders_for_momotaro(self):
        world = {"id": "momotaro", "genre": "momotaro"}
        html = action_catalog.catalog_panel_html(world, ROOT)
        self.assertIn("未実装（構想のみ）", html)
        self.assertIn("0.6", html)  # companionship_threshold filled in
        self.assertIn("catalog-kinds", html)

    def test_panel_degrades_instead_of_raising_on_a_mid_edit_world_yaml(self):
        # World.__init__ indexes required keys directly and raises
        # KeyError/TypeError on an incomplete world.yaml -- a day-to-day
        # state while someone is editing it in the browser. Every other
        # panel on this page already degrades instead of crashing the
        # whole /worlds/<id> route; this tab must too.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "projects" / "broken").mkdir(parents=True)
            (repo / "projects" / "broken" / "world.yaml").write_text(
                "protagonist: a\n", encoding="utf-8",
            )
            html = action_catalog.catalog_panel_html({"id": "broken", "genre": None}, repo)
        self.assertIn("世界設定が読み込めません", html)


if __name__ == "__main__":
    unittest.main()
