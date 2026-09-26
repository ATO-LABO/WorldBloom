"""viewer/world_expansion_view.py: read-only rendering of world-expansion
patches (WB-WORLDGROW-001 段階3b-1). Calls the render functions directly, the
way tests/test_world_prototype.py exercises world_prototype.render()."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from gapengine.world_patch import PATCH_RULES_VERSION
from viewer import world_expansion_view as wev

ROOT = Path(__file__).resolve().parents[1]


class JobPanelScriptTests(unittest.TestCase):
    """viewer/static/world-expansion.js's propose/check job panel: the state
    machine (what is disabled when, what a failed poll does) only exists in JS."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_job_panel_state_machine(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), str(root / "tests/world_expansion_ui.cjs"),
             str(root / "viewer/static/world-expansion.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DescribeAddTests(unittest.TestCase):
    def test_zone_with_note(self):
        add = {"zones": [{"name": "船大工の小屋", "parent": "海", "note": "浜の外れに残る、昔の船大工の作業小屋"}]}
        lines = wev.describe_add(add, {})
        self.assertEqual(lines, ["場所『船大工の小屋』を『海』の一部として足す — 浜の外れに残る、昔の船大工の作業小屋"])

    def test_zone_without_note(self):
        lines = wev.describe_add({"zones": [{"name": "祠", "parent": "海"}]}, {})
        self.assertEqual(lines, ["場所『祠』を『海』の一部として足す"])

    def test_item_with_give(self):
        add = {"items": [{"name": "潮見の貝殻", "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 2}]}]}
        lines = wev.describe_add(add, {})
        self.assertEqual(lines, ["品『潮見の貝殻』: 『海』で調べると手に入る（1 人 2 個まで）。渡すと受け手 +0.20／渡し手 +0.05"])

    def test_item_with_explicit_give_values(self):
        add = {"items": [{"name": "手鏡", "sources": [{"type": "investigate", "zone": "村", "count": 1, "max": 1}],
                          "give": {"receiver_affinity": 0.1, "giver_affinity": 0.02}}]}
        lines = wev.describe_add(add, {})
        self.assertIn("渡すと受け手 +0.10／渡し手 +0.02", lines[0])

    def test_item_keepsake_has_no_give_line(self):
        add = {"items": [{"name": "御守り", "sources": [{"type": "investigate", "zone": "村", "count": 1, "max": 1}],
                          "keepsake": True}]}
        lines = wev.describe_add(add, {})
        self.assertEqual(lines, ["品『御守り』: 『村』で調べると手に入る（1 人 1 個まで）。手放さない品（渡せない）"])

    def test_item_made_from(self):
        add = {"items": [{"name": "合わせ刃", "made_from": {"刃A": 2}}]}
        lines = wev.describe_add(add, {})
        self.assertEqual(lines, ["品『合わせ刃』: 『刃A』2 個から作る品"])
        self.assertNotIn("渡すと", lines[0])  # made_from items are excluded from the give budget/line

    def test_item_modifier_visible(self):
        add = {"items": [{"name": "鬼の金棒", "sources": [{"type": "investigate", "zone": "鬼ヶ島", "count": 1, "max": 1}],
                          "modifier": {"id": "item:鬼の金棒", "kind": "item", "value": 3, "visible": True}}]}
        lines = wev.describe_add(add, {})
        self.assertIn("持っていると対決の強さ +3（相手から見える）", lines[0])

    def test_item_modifier_not_visible(self):
        add = {"items": [{"name": "隠し剣", "sources": [{"type": "investigate", "zone": "村", "count": 1, "max": 1}],
                          "modifier": {"id": "item:隠し剣", "kind": "item", "value": 2, "visible": False}}]}
        lines = wev.describe_add(add, {})
        self.assertIn("相手には見えない", lines[0])

    def test_item_lootable_and_requires(self):
        add = {"items": [{"name": "秘薬", "sources": [{"type": "investigate", "zone": "村", "count": 1, "max": 1}],
                          "lootable": True, "requires": {"knowledge": "造船術"}}]}
        lines = wev.describe_add(add, {})
        self.assertIn("持ち主が倒れると奪われうる", lines[0])
        self.assertIn("『造船術』を知っている人だけが作れる", lines[0])

    def test_fact_with_fixed_implies(self):
        world = {"facts": [{"id": "鬼の弱点", "label": "鬼の弱点は{value}"}]}
        add = {"facts": [{"id": "小屋の言い伝え", "label": "昔の船大工は、鬼は塩を嫌うと語っていたらしい",
                          "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1}],
                          "secrecy": 0.3,
                          "implies": {"fact": "鬼の弱点", "value": "塩", "confidence": 0.3}}]}
        lines = wev.describe_add(add, world)
        self.assertEqual(lines, ["事実『小屋の言い伝え』（昔の船大工は、鬼は塩を嫌うと語っていたらしい）: "
                                  "『船大工の小屋』で調べると知る。話しやすさ 秘匿 0.30。"
                                  "『鬼の弱点』が『塩』だという手がかり（確信 0.30）"])

    def test_fact_label_with_value_placeholder_falls_back_to_id(self):
        # A label containing "{value}" is a template, not a fixed display name
        # -- describe_add must fall back to the id instead of using it as-is.
        world = {"facts": [{"id": "犯人", "label": "犯人は{value}"}]}
        add = {"facts": [{"id": "手がかりA", "label": "手がかりの中身",
                          "sources": [{"type": "investigate", "zone": "村", "count": 1}],
                          "implies": {"fact": "犯人", "value": "$truth", "confidence": 0.2}}]}
        lines = wev.describe_add(add, world)
        self.assertIn("『犯人』の本物を指す手がかり（確信 0.20）", lines[0])

    def test_fact_implies_truth_token(self):
        world = {"facts": [{"id": "犯人", "label": "犯人"}]}
        add = {"facts": [{"id": "手がかりA", "label": "怪しい足跡",
                          "sources": [{"type": "investigate", "zone": "村", "count": 1}],
                          "implies": {"fact": "犯人", "value": "$truth", "confidence": 0.2}}]}
        lines = wev.describe_add(add, world)
        self.assertIn("『犯人』の本物を指す手がかり（確信 0.20）", lines[0])

    def test_fact_implies_innocent_token(self):
        world = {"facts": [{"id": "犯人", "label": "犯人"}]}
        add = {"facts": [{"id": "手がかりB", "label": "曖昧な目撃談",
                          "sources": [{"type": "investigate", "zone": "村", "count": 1}],
                          "implies": {"fact": "犯人", "value": "$innocent:1", "confidence": 0.25}}]}
        lines = wev.describe_add(add, world)
        self.assertIn("『犯人』の無実の候補を指す、当てにならない手がかり（確信 0.25）", lines[0])

    def test_broken_zone_element_does_not_raise(self):
        lines = wev.describe_add({"zones": [{"name": "抜け"}]}, {})
        self.assertEqual(lines, ["読めない要素"])

    def test_broken_item_and_fact_elements_do_not_raise(self):
        lines = wev.describe_add({"items": [{}], "facts": [{"id": "x"}]}, {})
        self.assertEqual(lines, ["読めない要素", "読めない要素"])

    def test_non_dict_elements_do_not_raise(self):
        lines = wev.describe_add({"zones": ["oops"], "items": [None], "facts": [42]}, {})
        self.assertEqual(lines, ["読めない要素", "読めない要素", "読めない要素"])

    def test_no_additions_at_all(self):
        self.assertEqual(wev.describe_add({}, {}), ["何も追加しません"])

    def test_non_dict_add_does_not_raise(self):
        self.assertEqual(wev.describe_add(None, {}), ["読めない要素"])


def _proposal(patch, gate, *, patch_sha=None, gate_sha=None, error=None):
    raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
    return {
        "id": patch.get("id"), "patch": patch, "gate": gate, "error": error,
        "patch_sha256": patch_sha if patch_sha is not None else hashlib.sha256(raw).hexdigest(),
        "gate_sha256": gate_sha if gate_sha is not None else (
            hashlib.sha256(json.dumps(gate, ensure_ascii=False).encode("utf-8")).hexdigest() if gate else None),
    }


def _base_patch(**overrides):
    patch = {"id": "p-aaaa1111", "title": "海辺の小屋", "rationale": "海での空振りが多いので",
             "trigger": {"experiment": "exp-1", "zone": "海", "verb": "investigate", "count": 30, "whiffs": 20},
             "add": {"zones": [{"name": "小屋", "parent": "海"}]}}
    patch.update(overrides)
    return patch


def _reviewable_gate(patch, *, patch_sha, seed_set="holdout", rules_version=None):
    return {
        "patch_id": patch["id"], "patch_sha256": patch_sha, "status": "reviewable",
        "static": {"passed": True, "violations": []},
        "trial": {"evidence": {"seed_set": seed_set,
                                "patch_rules_version": rules_version if rules_version is not None else PATCH_RULES_VERSION},
                  "trigger": {"zone": "海", "verb": "investigate",
                              "base": {"count": 132, "whiffs": 132}, "patched": {"count": 135, "whiffs": 79}},
                  "pairs": [{"base": {"reached": True}, "patched": {"reached": True}},
                            {"base": {"reached": False}, "patched": {"reached": True}},
                            {"base": {"reached": True}, "patched": {"reached": False}},
                            {"base": {"reached": False}, "patched": {"reached": False}}],
                  "new_usage": {"decisions_in_new_zones": 3, "moves_into_new_zones": 2, "gathered_new_items": 1,
                                "gave_new_items": 0, "learned_new_facts": 0, "shared_new_facts": 0},
                  "contract": {"violations": []},
                  "reproduction": {"checked": 3, "identical": 3, "mismatched": []},
                  "reasons": ["到達 2→2（4本）"], "errors": []},
    }


class ApprovableTests(unittest.TestCase):
    def test_broken_proposal_is_not_approvable(self):
        proposal = {"error": "壊れています", "patch": None, "gate": None}
        ok, reason = wev.approvable(proposal)
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_no_gate_yet_is_trial_pending(self):
        patch = _base_patch()
        proposal = _proposal(patch, None)
        ok, reason = wev.approvable(proposal)
        self.assertFalse(ok)
        self.assertIn("試走", reason)

    def test_static_failed(self):
        patch = _base_patch()
        gate = {"patch_id": patch["id"], "patch_sha256": "x", "status": "static_failed",
                "static": {"passed": False, "violations": ["だめ"]}, "trial": None}
        proposal = _proposal(patch, gate)
        ok, reason = wev.approvable(proposal)
        self.assertFalse(ok)
        self.assertIn("静的ゲート", reason)

    def test_insufficient(self):
        patch = _base_patch()
        gate = {"patch_id": patch["id"], "patch_sha256": "x", "status": "insufficient", "static": {"passed": True}, "trial": {}}
        ok, reason = wev.approvable(_proposal(patch, gate))
        self.assertFalse(ok)
        self.assertIn("個体", reason)

    def test_reference_only(self):
        patch = _base_patch()
        gate = {"patch_id": patch["id"], "patch_sha256": "x", "status": "reference_only", "static": {"passed": True}, "trial": {}}
        ok, reason = wev.approvable(_proposal(patch, gate))
        self.assertFalse(ok)
        self.assertIn("参考試走", reason)

    def test_exploration_seed_is_not_approvable(self):
        patch = _base_patch()
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        gate = _reviewable_gate(patch, patch_sha=sha, seed_set="exploration")
        ok, reason = wev.approvable(_proposal(patch, gate, patch_sha=sha))
        self.assertFalse(ok)
        self.assertIn("holdout", reason)

    def test_stale_rules_version_is_not_approvable(self):
        patch = _base_patch()
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        gate = _reviewable_gate(patch, patch_sha=sha, rules_version=PATCH_RULES_VERSION - 1)
        ok, reason = wev.approvable(_proposal(patch, gate, patch_sha=sha))
        self.assertFalse(ok)
        self.assertIn("ルール", reason)

    def test_patch_changed_since_check_is_not_approvable(self):
        patch = _base_patch()
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        gate = _reviewable_gate(patch, patch_sha=sha)
        # The proposal's *current* file hash no longer matches what the gate checked.
        proposal = _proposal(patch, gate, patch_sha="different-hash-now")
        ok, reason = wev.approvable(proposal)
        self.assertFalse(ok)
        self.assertIn("変わって", reason)

    def test_reviewable_holdout_current_hash_is_approvable(self):
        patch = _base_patch()
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        gate = _reviewable_gate(patch, patch_sha=sha)
        ok, reason = wev.approvable(_proposal(patch, gate, patch_sha=sha))
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")


class ProposalCardTests(unittest.TestCase):
    def _make(self, *, can_write=True, seed_set="holdout", run_id=None, holdout_checks=None):
        patch = _base_patch(add={"items": [{"name": "潮見の貝殻",
                                            "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 2}]}]})
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        gate = _reviewable_gate(patch, patch_sha=sha, seed_set=seed_set)
        if holdout_checks is not None:
            gate["holdout_checks"] = holdout_checks
        proposal = _proposal(patch, gate, patch_sha=sha)
        html = wev.proposal_card(proposal, {}, world_id="momotaro", can_write=can_write, run_id=run_id)
        return html, proposal

    def test_what_is_added_appears_before_rationale(self):
        html, _ = self._make()
        self.assertLess(html.index("何が増えるか"), html.index("書き手の説明"))
        self.assertLess(html.index("何が増えるか"), html.index("検査の結果"))

    def test_whiff_rate_with_percentages(self):
        html, _ = self._make()
        self.assertIn("ベース 132 回中 132 回（100.0%）", html)
        self.assertIn("適用後 135 回中 79 回（58.5%）", html)

    def test_whiff_rate_zero_denominator_skips_percentage(self):
        table = wev._whiff_rate_side({"count": 0, "whiffs": 0})
        self.assertEqual(table, "0 回中 0 回")
        self.assertNotIn("%", table)

    def test_whiff_rate_zero_count_nonzero_whiffs_keeps_order(self):
        # R1: the zero-denominator branch used to swap count/whiffs.
        self.assertEqual(wev._whiff_rate_side({"count": 0, "whiffs": 5}), "0 回中 5 回")

    def test_whiff_rate_non_numeric_count_is_an_em_dash(self):
        # R1: a non-numeric count (e.g. None) must never be printed raw.
        self.assertEqual(wev._whiff_rate_side({"count": None, "whiffs": 5}), "—")
        self.assertEqual(wev._whiff_rate_side({"count": 5, "whiffs": None}), "—")

    def test_reach_table_counts(self):
        pairs = [{"base": {"reached": True}, "patched": {"reached": True}},
                 {"base": {"reached": False}, "patched": {"reached": True}},
                 {"base": {"reached": True}, "patched": {"reached": False}},
                 {"base": {"reached": False}, "patched": {"reached": False}}]
        table = wev._reach_table(pairs)
        self.assertEqual(table, {"成功維持": 1, "悪化": 1, "改善": 1, "失敗維持": 1})

    def test_html_escaping_of_title_rationale_and_violations(self):
        patch = _base_patch(title='<script>alert(1)</script>', rationale='"onmouseover=alert(1)"',
                             add={"zones": [{"name": "小屋", "parent": "海"}]})
        gate = {"patch_id": patch["id"], "patch_sha256": "x", "status": "static_failed",
                "static": {"passed": False, "violations": ['<img src=x onerror=alert(1)>']}, "trial": None}
        html = wev.proposal_card(_proposal(patch, gate), {}, world_id="momotaro", can_write=True)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&quot;onmouseover=alert(1)&quot;", html)

    def test_html_escaping_of_fact_label(self):
        patch = _base_patch(add={"facts": [{"id": "x", "label": '<b>bold</b>',
                                            "sources": [{"type": "investigate", "zone": "海", "count": 1}]}]})
        html = wev.proposal_card(_proposal(patch, None), {}, world_id="momotaro", can_write=True)
        self.assertNotIn("<b>bold</b>", html)
        self.assertIn("&lt;b&gt;", html)

    def test_can_write_false_hides_buttons(self):
        html, _ = self._make(can_write=False)
        self.assertNotIn("data-patch-action", html)
        self.assertIn("閲覧モードです", html)

    def test_can_write_true_shows_approve_and_reject_when_approvable(self):
        html, _ = self._make(can_write=True)
        self.assertIn('data-patch-action="approve"', html)
        self.assertIn('data-patch-action="reject"', html)

    def test_not_approvable_shows_disabled_approve_and_reject(self):
        # V3: a hidden approve control read as a bug when the reason was
        # visible but nothing you could click was -- now it's shown but
        # disabled, wired to the reason via aria-describedby.
        html, _ = self._make(can_write=True, seed_set="exploration")
        self.assertIn('data-patch-action="approve"', html)
        self.assertIn('data-patch-action="reject"', html)
        self.assertIn("承認できない理由", html)
        approve_start = html.index('data-patch-action="approve"')
        approve_tag = html[max(0, approve_start - 200):approve_start + 200]
        self.assertIn("disabled", approve_tag)
        self.assertIn("aria-describedby", approve_tag)

    def test_status_badge_uses_japanese_label_and_keeps_raw_token(self):
        # A3: the badge must never show a bare gate-status token like
        # "reviewable" as the visible label, but the raw token stays
        # available via data-status/title for anyone who needs it.
        html, _ = self._make(can_write=True, seed_set="holdout")
        self.assertIn("人の確認待ち", html)
        self.assertIn('data-status="reviewable"', html)
        self.assertIn('title="reviewable"', html)
        self.assertNotIn(">reviewable<", html)

    def test_unknown_status_falls_back_to_raw_escaped_token(self):
        self.assertEqual(wev._status_label("some_future_status"), "some_future_status")

    def test_reasons_are_collapsed_but_other_trial_facts_are_not(self):
        # V4: trial.reasons (developer-facing notes) go inside a collapsed
        # <details>; whiff rate/reach table/usage/contract/reproduction/seed
        # stay directly visible.
        html, _ = self._make()
        self.assertIn("<details><summary>検査の詳細（開発者向け）</summary>", html)
        details_start = html.index("<details><summary>検査の詳細")
        details_end = html.index("</details>", details_start)
        self.assertIn("到達 2→2（4本）", html[details_start:details_end])
        # The always-visible facts are outside that <details> block.
        self.assertNotIn("きっかけの空振り率", html[details_start:details_end])
        self.assertLess(html.index("きっかけの空振り率"), details_start)
        self.assertLess(html.index("到達の変化"), details_start)

    def test_broken_proposal_card_does_not_raise(self):
        html = wev.proposal_card({"id": "p-broken", "patch": None, "gate": None, "error": "読めません"},
                                 {}, world_id="momotaro", can_write=True)
        self.assertIn("読めません", html)
        self.assertIn("p-broken", html)

    def test_can_write_and_run_id_shows_check_button(self):
        html, proposal = self._make(can_write=True, run_id="exp-viewer")
        self.assertIn('data-patch-action="check"', html)
        self.assertIn(f'data-run="exp-viewer"', html)
        self.assertIn(f'data-patch="{proposal["id"]}"', html)
        self.assertIn("検査をやり直す", html)

    def test_can_write_false_hides_check_button_even_with_run_id(self):
        html, _ = self._make(can_write=False, run_id="exp-viewer")
        self.assertNotIn('data-patch-action="check"', html)

    def test_run_id_none_hides_check_button(self):
        html, _ = self._make(can_write=True, run_id=None)
        self.assertNotIn('data-patch-action="check"', html)

    def test_holdout_checks_zero_or_missing_is_not_shown(self):
        html_missing, _ = self._make()
        self.assertNotIn("holdout の検査", html_missing)
        html_zero, _ = self._make(holdout_checks=0)
        self.assertNotIn("holdout の検査", html_zero)

    def test_holdout_checks_two_is_shown(self):
        html, _ = self._make(holdout_checks=2)
        self.assertIn("holdout の検査: 2 回", html)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-world-expansion-view-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "world"
        self.project.mkdir()
        (self.project / "world.yaml").write_text("name: x\n", encoding="utf-8")

    def _snapshot(self):
        return {p: p.read_bytes() for p in self.project.rglob("*") if p.is_file()}

    def test_missing_patches_dir_returns_empty_state(self):
        before = self._snapshot()
        state = wev.load(self.project)
        self.assertEqual(state["approved"], [])
        self.assertEqual(state["proposed"], [])
        self.assertIsNone(state["error"])
        self.assertEqual(before, self._snapshot())
        self.assertFalse((self.project / "patches" / ".write.lock").exists())

    def test_broken_stack_json_surfaces_as_error_without_raising(self):
        (self.project / "patches").mkdir()
        (self.project / "patches" / "stack.json").write_text("not json", encoding="utf-8")
        before = self._snapshot()
        state = wev.load(self.project)
        self.assertEqual(state["approved"], [])
        self.assertTrue(state["error"])
        self.assertEqual(before, self._snapshot())

    def test_broken_proposal_yaml_is_reported_per_entry(self):
        proposed = self.project / "patches" / "_proposed"
        proposed.mkdir(parents=True)
        (proposed / "p-broken01.yaml").write_text("not: [valid, yaml", encoding="utf-8")
        before = self._snapshot()
        state = wev.load(self.project)
        self.assertIsNone(state["error"])
        self.assertEqual(len(state["proposed"]), 1)
        self.assertTrue(state["proposed"][0]["error"])
        self.assertEqual(before, self._snapshot())

    def test_broken_gate_json_is_reported_but_patch_still_parses(self):
        proposed = self.project / "patches" / "_proposed"
        proposed.mkdir(parents=True)
        patch = _base_patch()
        (proposed / f"{patch['id']}.yaml").write_text(
            yaml.safe_dump(patch, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (proposed / f"{patch['id']}.gate.json").write_text("not json", encoding="utf-8")
        state = wev.load(self.project)
        self.assertEqual(len(state["proposed"]), 1)
        entry = state["proposed"][0]
        self.assertIsNotNone(entry["patch"])
        self.assertTrue(entry["error"])

    def test_valid_proposal_round_trips(self):
        proposed = self.project / "patches" / "_proposed"
        proposed.mkdir(parents=True)
        patch = _base_patch()
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        (proposed / f"{patch['id']}.yaml").write_bytes(raw)
        gate = {"patch_id": patch["id"], "status": "trial_pending", "static": {"passed": True}, "trial": None}
        gate_raw = json.dumps(gate, ensure_ascii=False).encode("utf-8")
        (proposed / f"{patch['id']}.gate.json").write_bytes(gate_raw)
        state = wev.load(self.project)
        self.assertEqual(len(state["proposed"]), 1)
        entry = state["proposed"][0]
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["patch"]["id"], patch["id"])
        self.assertEqual(entry["patch_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(entry["gate_sha256"], hashlib.sha256(gate_raw).hexdigest())


class ApprovedListTests(unittest.TestCase):
    def test_empty_state_renders_nothing(self):
        self.assertEqual(wev.approved_list({"approved": [], "proposed": [], "head": "x"},
                                           world_id="w", can_write=True, run_link=lambda n: "#"), "")

    def test_approved_entries_render_with_reason_and_link(self):
        state = {"approved": [{"rev": 1, "patch": _base_patch(), "reason": "確認しました", "experiment": "exp-1"}],
                 "proposed": [], "head": "deadbeef"}
        html = wev.approved_list(state, world_id="momotaro", can_write=True,
                                 run_link=lambda name: f"/exp/{name}/monitor?tab=demand")
        self.assertIn("rev 1", html)
        self.assertIn("確認しました", html)
        self.assertIn('href="/exp/exp-1/monitor?tab=demand"', html)
        self.assertIn('data-patch-action="reopen"', html)

    def test_reopen_button_is_single_with_count_not_per_item(self):
        # R2: reopen() restores every approved revision at once -- the
        # button/copy must say "すべて" and carry the count, and there must
        # be exactly one such button regardless of how many approved items
        # there are (not one per item, as an earlier version had).
        state = {"approved": [
            {"rev": 1, "patch": _base_patch(), "reason": "ok1", "experiment": None},
            {"rev": 2, "patch": _base_patch(title="second"), "reason": "ok2", "experiment": None},
        ], "proposed": [], "head": "deadbeef"}
        html = wev.approved_list(state, world_id="momotaro", can_write=True, run_link=lambda n: "#")
        self.assertEqual(html.count('data-patch-action="reopen"'), 1)
        self.assertIn('data-count="2"', html)
        self.assertIn("承認済みの拡張をすべて提案中へ戻す（2件）", html)
        self.assertIn("2 件とも検査のやり直しが必要になります", html)

    def test_readonly_hides_reopen_button(self):
        state = {"approved": [{"rev": 1, "patch": _base_patch(), "reason": "ok", "experiment": None}],
                 "proposed": [], "head": "x"}
        html = wev.approved_list(state, world_id="momotaro", can_write=False, run_link=lambda n: "#")
        self.assertNotIn("data-patch-action", html)

    def test_approved_entry_with_non_string_experiment_does_not_raise(self):
        # A4: experiment came straight off patch.trigger.experiment in a
        # broken stack -- a non-string value must not crash run_link()/
        # _url_segment(), just render without a link.
        state = {"approved": [{"rev": 1, "patch": _base_patch(), "reason": "ok", "experiment": 12345}],
                 "proposed": [], "head": "x"}
        html = wev.approved_list(state, world_id="momotaro", can_write=True, run_link=lambda n: "#")
        self.assertIn("rev 1", html)
        self.assertNotIn('href="#"', html)

    def test_proposed_entries_show_title_status_trigger_and_link(self):
        # V1: each proposal gets its own line (title, Japanese status,
        # trigger zone/verb, and a link to the experiment that produced it)
        # instead of a bare "N 件あります" count.
        patch = _base_patch(add={"zones": [{"name": "祠", "parent": "海"}]})
        state = {"approved": [], "proposed": [{
            "id": patch["id"], "patch": patch,
            "gate": {"status": "reviewable"},
        }], "head": "x"}
        html = wev.approved_list(state, world_id="momotaro", can_write=False,
                                 run_link=lambda name: f"/exp/{name}/monitor?tab=demand")
        self.assertIn("提案中（1件）", html)
        self.assertIn("海辺の小屋", html)  # patch title from _base_patch
        self.assertIn("人の確認待ち", html)  # A3 label for "reviewable"
        self.assertIn("きっかけ: 海 で investigate", html)
        self.assertIn('href="/exp/exp-1/monitor?tab=demand"', html)
        self.assertIn("この実験の結果で確認する →", html)
        # No approve/reject on the world page (V1) -- that stays on the
        # experiment's own result screen.
        self.assertNotIn("data-patch-action=\"approve\"", html)
        self.assertNotIn("data-patch-action=\"reject\"", html)

    def test_proposed_entry_without_experiment_has_no_link(self):
        patch = _base_patch(trigger={"zone": "海", "verb": "investigate"})  # no "experiment" key
        state = {"approved": [], "proposed": [{"id": patch["id"], "patch": patch, "gate": {"status": "trial_pending"}}],
                 "head": "x"}
        html = wev.approved_list(state, world_id="momotaro", can_write=False, run_link=lambda n: "#")
        self.assertNotIn('href="#"', html)
        self.assertIn("試走待ち", html)

    def test_broken_proposed_entry_does_not_raise(self):
        state = {"approved": [], "proposed": [{"id": "p-broken", "patch": None, "error": "読めません"}], "head": "x"}
        html = wev.approved_list(state, world_id="momotaro", can_write=False, run_link=lambda n: "#")
        self.assertIn("p-broken", html)
        self.assertIn("読めません", html)


def _library_entry(entry_id="p-libbbbb", *, title="小屋案", world_name="放課後の約束", reason="良かった",
                   strong=2, weak=1, violations=(), broken=None):
    if broken is not None:
        return {"id": entry_id, "doc": None, "error": broken}
    doc = {
        "patch": {"id": entry_id, "title": title, "add": {"zones": [{"name": "小屋", "parent": "海"}]}},
        "provenance": {"world_name": world_name, "revision": {"approval": {"reason": reason}},
                      "evidence": {"usage": {"elites_strong": strong, "elites_weak": weak}}},
    }
    return {"id": entry_id, "doc": doc, "error": None, "violations": list(violations)}


class LibraryAssetSectionTests(unittest.TestCase):
    """WB-WORLDGROW-001 段階5d: approved_list()'s「ジャンルの資産」節 (state["library"])."""

    def test_library_only_still_renders_the_section(self):
        # 承認・提案・淘汰がゼロでも library が非空なら節は出る。
        state = {"approved": [], "proposed": [], "retired": [], "library": [_library_entry()], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=True, run_link=lambda n: "#")
        self.assertIn("ジャンルの資産（1件）", html)
        self.assertIn("小屋案", html)

    def test_library_missing_or_empty_matches_previous_bytes(self):
        # 3): 資産が空/未設定なら approved_list は従来（library キー無し）と
        # バイト完全一致 -- 既存の世界画面のテストを壊さない。
        state_no_key = {"approved": [{"rev": 1, "patch": _base_patch(), "reason": "ok", "experiment": None}],
                        "proposed": [], "head": "x"}
        state_empty = dict(state_no_key, library=[])
        html_no_key = wev.approved_list(state_no_key, world_id="momotaro", can_write=True, run_link=lambda n: "#")
        html_empty = wev.approved_list(state_empty, world_id="momotaro", can_write=True, run_link=lambda n: "#")
        self.assertEqual(html_no_key, html_empty)
        self.assertNotIn("ジャンルの資産", html_no_key)
        # 承認・提案・淘汰・資産のすべてが無ければ空文字のまま (既存挙動)。
        self.assertEqual(wev.approved_list({"approved": [], "proposed": [], "retired": [], "library": []},
                                           world_id="w", can_write=True, run_link=lambda n: "#"), "")

    def test_fit_empty_shows_import_button_with_entry_and_head(self):
        state = {"approved": [], "proposed": [], "retired": [],
                 "library": [_library_entry(violations=[])], "head": "deadbeef"}
        html = wev.approved_list(state, world_id="momotaro2", can_write=True, run_link=lambda n: "#")
        self.assertIn("取り込めます", html)
        self.assertIn('data-patch-action="import"', html)
        self.assertIn('data-entry="p-libbbbb"', html)
        self.assertIn('data-head="deadbeef"', html)
        self.assertIn('data-world="momotaro2"', html)

    def test_violation_shows_only_the_first_and_hides_the_button(self):
        state = {"approved": [], "proposed": [], "retired": [],
                 "library": [_library_entry(violations=["一つ目の違反", "二つ目の違反"])], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=True, run_link=lambda n: "#")
        self.assertIn("一つ目の違反", html)
        self.assertNotIn("二つ目の違反", html)
        self.assertNotIn('data-patch-action="import"', html)

    def test_broken_entry_shows_its_error_and_does_not_raise(self):
        state = {"approved": [], "proposed": [], "retired": [],
                 "library": [_library_entry(broken="形式が不正です")], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=True, run_link=lambda n: "#")
        self.assertIn("形式が不正です", html)
        self.assertNotIn('data-patch-action="import"', html)

    def test_can_write_false_hides_the_import_button_even_when_fit(self):
        state = {"approved": [], "proposed": [], "retired": [],
                 "library": [_library_entry(violations=[])], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=False, run_link=lambda n: "#")
        self.assertNotIn('data-patch-action="import"', html)

    def test_xss_in_title_world_name_reason_usage_and_violation_is_escaped(self):
        evil = '<script>alert(1)</script>"'
        state = {"approved": [], "proposed": [], "retired": [], "library": [
            _library_entry(entry_id="p-libaaaa", title=evil, world_name=evil, reason=evil,
                          strong=evil, weak=evil, violations=[evil]),
        ], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=True, run_link=lambda n: "#")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertGreaterEqual(html.count("&lt;script&gt;alert(1)&lt;/script&gt;"), 4)

    def test_applied_id_shows_status_instead_of_fit_or_button(self):
        # M2 (Opus review): a library entry whose id is already active in
        # THIS world (e.g. the origin world looking at its own export) must
        # say so, ahead of fit() -- never "取り込めます" or a stray
        # duplicate-id violation, and never a button (import_patch() itself
        # would reject this exact id for the exact same reason).
        state = {"approved": [{"rev": 1, "patch": {"id": "p-dup0000", "title": "元の拡張"}, "reason": "ok",
                              "experiment": None}],
                 "proposed": [], "retired": [],
                 "library": [_library_entry(entry_id="p-dup0000", violations=[])], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=True, run_link=lambda n: "#")
        self.assertIn("この世界で適用中です", html)
        self.assertNotIn("取り込めます", html)
        self.assertNotIn('data-patch-action="import"', html)

    def test_proposed_id_shows_status_instead_of_fit_or_button(self):
        state = {"approved": [], "retired": [],
                 "proposed": [{"id": "p-dup0001", "patch": _base_patch(id="p-dup0001"), "gate": None}],
                 "library": [_library_entry(entry_id="p-dup0001", violations=[])], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=True, run_link=lambda n: "#")
        self.assertIn("この世界で提案中です", html)
        self.assertNotIn('data-patch-action="import"', html)

    def test_retired_id_shows_status_instead_of_fit_or_button(self):
        state = {"approved": [], "proposed": [],
                 "retired": [{"rev": 1, "patch": {"id": "p-dup0002", "title": "枯れた"}, "reason": "使われず",
                             "experiment": None}],
                 "library": [_library_entry(entry_id="p-dup0002", violations=[])], "head": "x"}
        html = wev.approved_list(state, world_id="w", can_write=True, run_link=lambda n: "#")
        self.assertIn("この世界で淘汰済みです", html)
        self.assertNotIn('data-patch-action="import"', html)


class LibraryProposalCardTests(unittest.TestCase):
    """WB-WORLDGROW-001 段階5d: proposal_card()'s author.backend=="library" branch."""

    def test_library_origin_shows_where_it_came_from_instead_of_a_trigger(self):
        patch = _base_patch(title="輸入された拡張", trigger={"zone": "海", "verb": "investigate"},
                            author={"backend": "library", "origin": {"world_id": "momotaro", "world_name": "桃太郎"}})
        html = wev.proposal_card(_proposal(patch, None), {}, world_id="momotaro2", can_write=False)
        self.assertIn("ジャンルの資産『輸入された拡張』", html)
        self.assertIn("元: 世界『桃太郎』", html)
        self.assertIn("検査は未実施", html)
        # The old trigger-line format (experiment/count/whiffs) must not
        # leak through for a library-origin proposal that has none of those.
        self.assertNotIn("実験『None』", html)

    def test_library_origin_escapes_title_and_origin_world_name(self):
        evil = '<script>alert(1)</script>"'
        patch = _base_patch(title=evil, author={"backend": "library", "origin": {"world_name": evil}})
        html = wev.proposal_card(_proposal(patch, None), {}, world_id="w", can_write=False)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)


class ApprovedListLibraryProposalHintTests(unittest.TestCase):
    """WB-WORLDGROW-001 段階5d R2 (Opus review): approved_list()'s 提案中
    list points a library-imported proposal at where it can actually be
    checked, via the optional library_hint experiment name."""

    def _state(self):
        patch = _base_patch(author={"backend": "library", "origin": {"world_name": "元の世界"}})
        return {"approved": [], "retired": [], "proposed": [{"id": patch["id"], "patch": patch, "gate": None}],
               "head": "x"}

    def test_hint_text_without_a_link_when_no_matching_experiment(self):
        html = wev.approved_list(self._state(), world_id="w", can_write=False, run_link=lambda n: f"/exp/{n}")
        self.assertIn("検査は最新の実験の結果画面の「世界の需要と拡張」タブで行えます。", html)
        self.assertNotIn("その実験の結果を見る", html)

    def test_hint_links_to_the_given_experiment_when_present(self):
        html = wev.approved_list(self._state(), world_id="w", can_write=False,
                                 run_link=lambda n: f"/exp/{n}/monitor?tab=demand", library_hint="exp-42")
        self.assertIn('href="/exp/exp-42/monitor?tab=demand"', html)
        self.assertIn("その実験の結果を見る →", html)


if __name__ == "__main__":
    unittest.main()
