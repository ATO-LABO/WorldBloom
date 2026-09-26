"""読むだけの世界拡張パッチ表示 (WB-WORLDGROW-001 段階3b-1).

承認・却下・差し戻しの実際の書き込みは execution/world_patch_approval.py と
viewer/library_pages.py の /api/worlds/... が担う。ここは patches/ を読み直して
HTML を組み立てるだけで、一切書き込まない（patch_lock も取らない）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from gapengine.world_patch import (
    DEFAULT_GIVER_AFFINITY,
    DEFAULT_RECEIVER_AFFINITY,
    PATCH_RULES_VERSION,
    PatchError,
    read_stack,
    retired_patches,
    verify_stack,
)
from viewer import pages

_escape = pages._escape

# --------------------------------------------------------------------------
# Reading state
# --------------------------------------------------------------------------


def _load_proposal(path: Path) -> dict:
    entry: dict[str, Any] = {"id": path.stem, "patch": None, "gate": None,
                              "patch_sha256": None, "gate_sha256": None, "error": None}
    try:
        raw = path.read_bytes()
        entry["patch_sha256"] = hashlib.sha256(raw).hexdigest()
        patch = yaml.safe_load(raw)
        if not isinstance(patch, dict):
            raise ValueError("パッチの形式が不正です")
        entry["patch"] = patch
    except (OSError, yaml.YAMLError, ValueError) as error:
        entry["error"] = f"提案を読み込めません: {error}"
        return entry
    gate_path = path.with_suffix(".gate.json")
    if gate_path.is_file():
        try:
            gate_raw = gate_path.read_bytes()
            entry["gate_sha256"] = hashlib.sha256(gate_raw).hexdigest()
            gate = json.loads(gate_raw)
            if not isinstance(gate, dict):
                raise ValueError("検査結果の形式が不正です")
            entry["gate"] = gate
        except (OSError, ValueError) as error:
            entry["error"] = f"検査結果を読み込めません: {error}"
    return entry


def load(project_dir) -> dict:
    """その世界の拡張の状態をサーバー側で読み直す。書き込みは一切しない。"""
    project_dir = Path(project_dir)
    result: dict[str, Any] = {"approved": [], "proposed": [], "retired": [], "head": None, "error": None}
    try:
        verified = verify_stack(project_dir)
        retired = retired_patches(project_dir)
        stack = read_stack(project_dir)
    except PatchError as error:
        result["error"] = str(error)
        return result
    # WB-WORLDGROW-001 段階5a: a retire revision breaks the old 1:1
    # zip(verified, stack["revisions"]) -- match by patch_id instead of
    # position (stack["revisions"] now interleaves "patch" and "retire" kinds).
    approval_by_id = {r["patch_id"]: r for r in stack["revisions"] if r.get("kind", "patch") == "patch"}
    for patch, _raw in verified:
        revision = approval_by_id.get(patch["id"]) or {}
        result["approved"].append({
            "rev": revision.get("rev"), "patch": patch,
            "reason": (revision.get("approval") or {}).get("reason") or "",
            "approved_at": revision.get("approved_at"),
            "experiment": (patch.get("trigger") or {}).get("experiment"),
        })
    for entry in retired:
        result["retired"].append({
            "rev": entry["rev"], "patch": entry["patch"],
            "reason": entry["retire"].get("reason") or "",
            "retired_at": entry["retire"].get("retired_at"),
            "experiment": entry["retire"].get("experiment"),
        })
    result["head"] = stack["head"]
    proposed_dir = project_dir / "patches" / "_proposed"
    if proposed_dir.is_dir():
        for path in sorted(proposed_dir.glob("*.yaml")):
            result["proposed"].append(_load_proposal(path))
    return result


# --------------------------------------------------------------------------
# Approvability (display judgement only -- approve() has the final say)
# --------------------------------------------------------------------------

_STATUS_REASONS = {
    "static_failed": "静的ゲートで不採用です（下の違反を参照）",
    "trial_pending": "試走がまだです",
    "contract_failed": "契約検査の違反があります",
    "insufficient": "試走の個体数が足りません（3個体以上が必要）",
    "reference_only": "凍結入力の無い実験での参考試走なので、承認の根拠に使えません",
}

# A3 (viewer review): the badge must never show a bare gate-status token.
# Unknown values fall back to the raw (escaped) token.
_STATUS_LABELS = {
    "static_failed": "静的ゲートで不採用", "trial_pending": "試走待ち",
    "contract_failed": "契約検査で不採用", "reference_only": "参考試走のみ",
    "insufficient": "試走の規模不足", "reviewable": "人の確認待ち",
}


def _status_label(status: Any) -> str:
    return _STATUS_LABELS.get(status, status)


def _status_badge(status: Any) -> str:
    token = _escape(status)
    return f'<span class="we-status" data-status="{token}" title="{token}">{_escape(_status_label(status))}</span>'


def approvable(proposal: dict) -> tuple[bool, str]:
    if proposal.get("error"):
        return False, "読み込めない提案です"
    patch = proposal.get("patch")
    gate = proposal.get("gate")
    if not isinstance(patch, dict):
        return False, "読み込めない提案です"
    if not isinstance(gate, dict):
        return False, "試走がまだです"
    status = gate.get("status")
    if status in _STATUS_REASONS:
        return False, _STATUS_REASONS[status]
    if status != "reviewable":
        return False, f"承認できる状態ではありません（{status}）"
    evidence = ((gate.get("trial") or {}).get("evidence")) or {}
    seed_set = evidence.get("seed_set")
    if seed_set == "exploration":
        return False, "探索用の seed での試走です。承認には holdout の検査が要ります"
    if seed_set != "holdout":
        return False, "holdout の検査が必要です"
    if evidence.get("patch_rules_version") != PATCH_RULES_VERSION:
        return False, "検査のルールが新しくなりました。検査をやり直してください"
    if gate.get("patch_sha256") != proposal.get("patch_sha256"):
        return False, "検査のあとにパッチが変わっています。検査をやり直してください"
    return True, ""


# --------------------------------------------------------------------------
# "何が増えるか" in plain Japanese
# --------------------------------------------------------------------------


def _num(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fact_label(world: Any, fact_id: Any) -> str:
    if isinstance(world, dict):
        for fact in world.get("facts") or []:
            if isinstance(fact, dict) and fact.get("id") == fact_id:
                label = fact.get("label")
                if isinstance(label, str) and "{value}" not in label:
                    return label
    return fact_id if isinstance(fact_id, str) else "?"


def _zone_text(zone: dict) -> str:
    try:
        name, parent = zone["name"], zone["parent"]
        if not isinstance(name, str) or not isinstance(parent, str):
            raise ValueError
        text = f"場所『{name}』を『{parent}』の一部として足す"
        note = zone.get("note")
        if isinstance(note, str) and note:
            text += f" — {note}"
        return text
    except Exception:
        return "読めない要素"


def _item_text(item: dict) -> str:
    try:
        name = item["name"]
        if not isinstance(name, str):
            raise ValueError
        made_from = item.get("made_from")
        has_made_from = isinstance(made_from, dict) and bool(made_from)
        sources = item.get("sources")
        if has_made_from:
            acquire = "・".join(f"『{material}』{_num(count)} 個" for material, count in made_from.items()) + "から作る品"
        elif isinstance(sources, list) and sources:
            acquire = "、".join(
                f"『{source.get('zone')}』で調べると手に入る（1 人 {_num(source.get('max'))} 個まで）"
                for source in sources if isinstance(source, dict)
            )
        else:
            acquire = "入手手段が不明"
        text = f"品『{name}』: {acquire}"
        extra = []
        if item.get("keepsake"):
            extra.append("手放さない品（渡せない）")
        modifier = item.get("modifier")
        if isinstance(modifier, dict):
            visible = "相手から見える" if modifier.get("visible") else "相手には見えない"
            extra.append(f"持っていると対決の強さ +{_num(modifier.get('value'))}（{visible}）")
        if item.get("lootable"):
            extra.append("持ち主が倒れると奪われうる")
        requires = item.get("requires")
        if isinstance(requires, dict) and isinstance(requires.get("knowledge"), str):
            extra.append(f"『{requires['knowledge']}』を知っている人だけが作れる")
        if not item.get("keepsake") and not has_made_from:
            give = item.get("give") if isinstance(item.get("give"), dict) else {}
            receiver = give.get("receiver_affinity", DEFAULT_RECEIVER_AFFINITY)
            giver = give.get("giver_affinity", DEFAULT_GIVER_AFFINITY)
            extra.append(f"渡すと受け手 +{float(receiver):.2f}／渡し手 +{float(giver):.2f}")
        if extra:
            text += "。" + "。".join(extra)
        return text
    except Exception:
        return "読めない要素"


def _relation_text(relation: dict, world: Any, *, negate: bool) -> str | None:
    if not isinstance(relation, dict):
        return None
    target, value, confidence = relation.get("fact"), relation.get("value"), relation.get("confidence")
    target_label = _fact_label(world, target)
    conf_text = (f"（確信 {float(confidence):.2f}）"
                 if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else "")
    if value == "$truth":
        return f"『{target_label}』の{'本物ではない' if negate else '本物を指す'}手がかり{conf_text}"
    if isinstance(value, str) and value.startswith("$innocent:"):
        return f"『{target_label}』の無実の候補を{'否定する' if negate else '指す、当てにならない'}手がかり{conf_text}"
    verb = "ではないという" if negate else "だという"
    return f"『{target_label}』が『{value}』{verb}手がかり{conf_text}"


def _fact_text(fact: dict, world: Any) -> str:
    try:
        fact_id, label = fact["id"], fact["label"]
        if not isinstance(fact_id, str) or not isinstance(label, str):
            raise ValueError
        sources = fact.get("sources")
        zones = ("、".join(f"『{s.get('zone')}』" for s in sources if isinstance(s, dict))
                 if isinstance(sources, list) else "")
        parts = [f"{zones}で調べると知る" if zones else "入手手段が不明"]
        secrecy = fact.get("secrecy")
        if isinstance(secrecy, (int, float)) and not isinstance(secrecy, bool):
            parts.append(f"話しやすさ 秘匿 {float(secrecy):.2f}")
        share = fact.get("share_min_affinity")
        if isinstance(share, (int, float)) and not isinstance(share, bool):
            parts.append(f"共有には好感度 {float(share):+.2f} 以上が必要")
        implies_text = _relation_text(fact.get("implies"), world, negate=False)
        if implies_text:
            parts.append(implies_text)
        refutes_text = _relation_text(fact.get("refutes"), world, negate=True)
        if refutes_text:
            parts.append(refutes_text)
        return f"事実『{fact_id}』（{label}）: " + "。".join(parts)
    except Exception:
        return "読めない要素"


def describe_add(add: Any, world: Any) -> list[str]:
    if not isinstance(add, dict):
        return ["読めない要素"]
    lines: list[str] = []
    for zone in add.get("zones") or []:
        lines.append(_zone_text(zone) if isinstance(zone, dict) else "読めない要素")
    for item in add.get("items") or []:
        lines.append(_item_text(item) if isinstance(item, dict) else "読めない要素")
    for fact in add.get("facts") or []:
        lines.append(_fact_text(fact, world) if isinstance(fact, dict) else "読めない要素")
    return lines or ["何も追加しません"]


# --------------------------------------------------------------------------
# Trial evidence in plain Japanese
# --------------------------------------------------------------------------


def _reach_table(pairs: list) -> dict[str, int]:
    """成功維持／悪化／改善／失敗維持 の4つの数。scripts/world_patch.py の
    _print_gate_summary と同じ数え方 -- 表示側の純関数としてここに置く
    （scripts/world_patch.py 自体は変更しない）。"""
    table = {"成功維持": 0, "悪化": 0, "改善": 0, "失敗維持": 0}
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        before = bool((pair.get("base") or {}).get("reached"))
        after = bool((pair.get("patched") or {}).get("reached"))
        table["成功維持" if before and after else "悪化" if before else
              "改善" if after else "失敗維持"] += 1
    return table


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _whiff_rate_side(side: Any) -> str:
    if not isinstance(side, dict):
        return "—"
    count, whiffs = side.get("count"), side.get("whiffs")
    # R1 (viewer review): the order is always "count 回中 whiffs 回" -- a
    # previous version swapped them on the zero-denominator branch, and
    # printed a non-numeric count/whiffs (e.g. None) raw instead of "—".
    if not _is_number(count) or not _is_number(whiffs):
        return "—"
    if count <= 0:
        return f"{count} 回中 {whiffs} 回"
    return f"{count} 回中 {whiffs} 回（{whiffs / count * 100:.1f}%）"


def _whiff_rate_text(trigger: Any) -> str:
    if not isinstance(trigger, dict):
        return ""
    return f"ベース {_whiff_rate_side(trigger.get('base'))} → 適用後 {_whiff_rate_side(trigger.get('patched'))}"


# WB-WORLDGROW-002 stage 4 (段階4への追加、段階3 294ddbcの申し送り):
# ignorance/blocked の trial["trigger"] は base/patched の形が whiff と違う
# (gapengine/world_patch_trial.py の _ignorance_counts/_blocked_counts) --
# 種類ごとの文言で表示する。whiff は _whiff_rate_side/_whiff_rate_text の
# ままバイト一致（この2関数は変更していない）。
def _ignorance_rate_side(side: Any) -> str:
    if not isinstance(side, dict):
        return "—"
    count, total = side.get("count"), side.get("total")
    if not _is_number(count) or not _is_number(total):
        return "—"
    if total <= 0:
        return f"{total} 回中 {count} 回"
    share = side.get("share")
    # 必須3の補足（段階4 review 1）: 分母（total）が「その場所の道筋付き決定」
    # であることを明示する。
    pct = f"（{float(share) * 100:.1f}%）" if _is_number(share) else ""
    return f"その場所の道筋付き決定 {total} 回中、手探り {count} 回{pct}"


def _blocked_rate_side(side: Any) -> str:
    if not isinstance(side, dict):
        return "—"
    count, lost_total = side.get("count"), side.get("lost_total")
    if not _is_number(count) or not _is_number(lost_total):
        return "—"
    if lost_total <= 0:
        return f"{lost_total} 回中 {count} 回"
    # R2 (段階3 review 1): trial側は "lost_rate"（gapengine/world_patch_trial.
    # py's _blocked_counts）。需要トリガーの "lost_share"（意味が違う）とは
    # 別名にして取り違えを防ぐ。
    # 必須3（段階4 review 1）: lost_rate は「count 回中 lost_total 回」の割合
    # ではなく、道筋付き決定の全体に対する見通しなしの割合 -- count と
    # lost_total が同じ値でも 100% にならない。分母が何かを明示し、%も
    # 「count 回中の割合」ではなく「道筋付き決定全体に対する割合」と分けて書く。
    lost_rate = side.get("lost_rate")
    pct = f"（道筋付き決定の{float(lost_rate) * 100:.1f}%が見通しなし）" if _is_number(lost_rate) else ""
    return f"見通しなし {lost_total} 回のうちこのきっかけが原因 {count} 回{pct}"


_TRIGGER_RATE_LABELS = {"ignorance": "きっかけの手探り", "blocked": "きっかけの見通しなし決定"}


def _trigger_rate_label(trigger: Any) -> str:
    kind = trigger.get("kind", "whiff") if isinstance(trigger, dict) else "whiff"
    return _TRIGGER_RATE_LABELS.get(kind, "きっかけの空振り率")


def _trigger_rate_text(trigger: Any) -> str:
    if not isinstance(trigger, dict):
        return ""
    kind = trigger.get("kind", "whiff")
    if kind == "ignorance":
        return (f"ベース {_ignorance_rate_side(trigger.get('base'))} → "
                f"適用後 {_ignorance_rate_side(trigger.get('patched'))}")
    if kind == "blocked":
        return (f"ベース {_blocked_rate_side(trigger.get('base'))} → "
                f"適用後 {_blocked_rate_side(trigger.get('patched'))}")
    return _whiff_rate_text(trigger)


_USAGE_LABELS = (
    ("decisions_in_new_zones", "新しい場所での決定"),
    ("moves_into_new_zones", "新しい場所への移動"),
    ("gathered_new_items", "新しい品を集めた"),
    ("gave_new_items", "新しい品を渡した"),
    ("learned_new_facts", "新しい事実を知った"),
    ("shared_new_facts", "新しい事実を話した"),
)


_USAGE_SOURCES_ONLY_LABELS = {
    "gathered_new_items": "既存の品を新しい場所で集めた",
    "learned_new_facts": "既存の事実を新しい場所で知った",
}


def _trial_html(trial: dict, add: dict | None = None) -> str:
    # R5（段階4 review 1）: add.items/add.facts を持たない（add.sources だけ
    # の）パッチでは、gathered_new_items/learned_new_facts は「新しい品/事実」
    # ではなく「既存の品/事実を新しい場所で」の回数（viewer/world_usage_badge.
    # py の同じ切り替えと揃える）。
    items_added_by_name = bool((add or {}).get("items"))
    facts_added_by_name = bool((add or {}).get("facts"))

    def usage_label(key, label):
        if key == "gathered_new_items" and not items_added_by_name:
            return _USAGE_SOURCES_ONLY_LABELS[key]
        if key == "learned_new_facts" and not facts_added_by_name:
            return _USAGE_SOURCES_ONLY_LABELS[key]
        return label

    parts = []
    trigger_result = trial.get("trigger")
    if trigger_result:
        parts.append(f"<p>{_escape(_trigger_rate_label(trigger_result))}: {_escape(_trigger_rate_text(trigger_result))}</p>")
    pairs = trial.get("pairs") or []
    if pairs:
        table = _reach_table(pairs)
        parts.append("<p>到達の変化: " + "、".join(f"{_escape(k)}={_escape(v)}" for k, v in table.items()) + "</p>")
    usage = trial.get("new_usage") or {}
    if usage:
        parts.append("<p>足したものの使用回数: " + "、".join(
            f"{usage_label(key, label)} {_escape(usage.get(key, 0))}回" for key, label in _USAGE_LABELS) + "</p>")
    contract_violations = (trial.get("contract") or {}).get("violations") or []
    if contract_violations:
        parts.append("<p>契約検査の違反:</p><ul>" + "".join(f"<li>{_escape(v)}</li>" for v in contract_violations) + "</ul>")
    reproduction = trial.get("reproduction") or {}
    if reproduction:
        parts.append(f"<p>再現確認: {_escape(reproduction.get('identical', 0))}/{_escape(reproduction.get('checked', 0))}</p>")
    seed_set = ((trial.get("evidence") or {}).get("seed_set"))
    if seed_set:
        parts.append(f"<p>seed: {'holdout（承認可）' if seed_set == 'holdout' else 'exploration（探索用、承認不可）'}</p>")
    # V4 (viewer review): these lines are developer-facing notes recorded
    # verbatim by run_trial ("shaped は枝の追加で尺度が変わるため参考値" etc.)
    # -- collapsed by default so they read as detail, not as the headline.
    reasons = trial.get("reasons") or []
    if reasons:
        parts.append("<details><summary>検査の詳細（開発者向け）</summary><ul>"
                     + "".join(f"<li>{_escape(r)}</li>" for r in reasons) + "</ul></details>")
    errors = trial.get("errors") or []
    if errors:
        parts.append("<p>エラー:</p><ul>" + "".join(
            f"<li>{_escape(json.dumps(e, ensure_ascii=False))}</li>" for e in errors) + "</ul>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def proposal_card(proposal: dict, world: Any, *, world_id: str, can_write: bool, run_id: str | None = None) -> str:
    pid = proposal.get("id")
    patch = proposal.get("patch")
    if proposal.get("error") or not isinstance(patch, dict):
        return (f'<article class="card we-card we-card-broken" data-patch-card="{_escape(pid)}">'
                f'<h3>{_escape(pid)}</h3>'
                f'<p class="muted">{_escape(proposal.get("error") or "読み込めない提案です")}</p></article>')

    gate = proposal.get("gate") or {}
    status = gate.get("status", "trial_pending")
    ok, reason = approvable(proposal)
    add_lines = describe_add(patch.get("add") or {}, world)
    trigger = patch.get("trigger") or {}
    trigger_html = ""
    if trigger:
        trigger_html = (f"実験『{_escape(trigger.get('experiment'))}』の『{_escape(trigger.get('zone'))}』で"
                         f"『{_escape(trigger.get('verb'))}』が {_escape(trigger.get('count'))} 回中 "
                         f"{_escape(trigger.get('whiffs'))} 回空振り")

    result_parts = []
    static = gate.get("static") or {}
    violations = static.get("violations") or []
    if violations:
        result_parts.append("<p>静的ゲートの違反:</p><ul>" + "".join(f"<li>{_escape(v)}</li>" for v in violations) + "</ul>")
    trial = gate.get("trial")
    if isinstance(trial, dict):
        result_parts.append(_trial_html(trial, patch.get("add")))
    elif not violations:
        result_parts.append("<p>試走がまだです。</p>")
    holdout_checks = gate.get("holdout_checks")
    if isinstance(holdout_checks, int) and not isinstance(holdout_checks, bool) and holdout_checks >= 1:
        result_parts.append(f"<p>holdout の検査: {_escape(holdout_checks)} 回</p>")
    results_html = "".join(result_parts) or "<p>検査結果がありません。</p>"

    # V3 (viewer review): the reason id exists regardless of can_write so
    # aria-describedby has something to point at once the button appears.
    reason_id = f"we-reason-{_escape(pid)}"
    reason_html = (f'<p class="we-reason" id="{reason_id}">承認できない理由: {_escape(reason)}</p>'
                   if not ok else "")
    rationale_html = ("<h4>書き手の説明（中身と食い違うことがあります。上の内容で判断してください）</h4>"
                       f"<p>{_escape(patch.get('rationale') or '')}</p>")

    if can_write:
        patch_sha = _escape(proposal.get("patch_sha256") or "")
        gate_sha = _escape(proposal.get("gate_sha256") or "")
        # V3 (viewer review): the approval block is always shown when
        # can_write -- a hidden reason with no visible control read as a
        # bug. When not approvable, textarea+button are disabled and point
        # at the "承認できない理由" paragraph via aria-describedby.
        disabled = "" if ok else " disabled"
        describedby = "" if ok else f' aria-describedby="{reason_id}"'
        approve_block = (
            f'<textarea data-approve-reason placeholder="承認理由（10文字以上）" minlength="10"'
            f'{disabled}{describedby}></textarea>'
            f'<button type="button" data-patch-action="approve" data-world="{_escape(world_id)}" '
            f'data-patch="{_escape(pid)}" data-patch-sha="{patch_sha}" data-gate-sha="{gate_sha}"'
            f'{disabled}{describedby}>承認</button>'
        )
        reject_block = (
            f'<button type="button" data-patch-action="reject" data-world="{_escape(world_id)}" '
            f'data-patch="{_escape(pid)}" data-patch-sha="{patch_sha}" data-gate-sha="{gate_sha}">却下</button>'
        )
        check_block = ""
        if run_id:
            # WB-WORLDGROW-001 段階3b-3: re-run the holdout trial without
            # touching approve/reject -- useful before a proposal is
            # approvable at all (trial_pending) and after (rules changed,
            # patch edited).
            check_block = (
                f'<button type="button" data-patch-action="check" data-run="{_escape(run_id)}" '
                f'data-patch="{_escape(pid)}">検査をやり直す</button>'
                '<p class="muted we-hint">承認に使う seed（holdout）でもう一度試走します。数分かかります。</p>'
            )
        actions_html = ('<div class="we-actions">' + approve_block + reject_block + check_block
                         + '<p class="we-message" data-patch-message role="alert"></p></div>')
    else:
        actions_html = '<p class="muted">閲覧モードです。承認や却下は実行管理を有効にして開いたときに行えます。</p>'

    return (
        f'<article class="card we-card" data-patch-card="{_escape(pid)}">'
        f'<h3>{_escape(patch.get("title"))} {_status_badge(status)}</h3>'
        f'<p class="we-id muted">{_escape(pid)}</p>'
        "<h4>何が増えるか</h4><ul>" + "".join(f"<li>{_escape(line)}</li>" for line in add_lines) + "</ul>"
        + (f"<h4>きっかけ</h4><p>{trigger_html}</p>" if trigger_html else "")
        + f"<h4>検査の結果</h4>{results_html}"
        + reason_html + rationale_html + actions_html
        + "</article>"
    )


def _experiment_link_html(experiment: Any, run_link, label: str) -> str:
    """A4 (viewer review): `experiment` comes straight off a patch's own
    trigger data, which a broken stack.json/proposal could hold as anything
    -- only build the link (and call run_link/_url_segment, which require a
    str) when it actually is one."""
    if isinstance(experiment, str) and experiment:
        return f' <a href="{_escape(run_link(experiment))}">{_escape(label)}</a>'
    return ""


def approved_list(state: dict, *, world_id: str, can_write: bool, run_link, world: Any = None) -> str:
    """「後から生まれたもの」節全体: 承認済みの適用順一覧 + 提案中の内訳。
    承認済み・提案中がどちらも0件なら空文字（既存の世界画面のテストが期待
    するHTMLを壊さない）。承認・却下の操作はここには置かない（実験の結果
    画面でやる、V1）。

    `world`（world.yaml の生データ）は事実の表示名解決に使う任意の追加引数
    （渡さなければ id をそのまま使う）。"""
    approved = state.get("approved") or []
    proposed = state.get("proposed") or []
    retired = state.get("retired") or []
    if not approved and not proposed and not retired:
        return ""
    parts = ['<section class="card we-approved"><h2>後から生まれたもの</h2>']
    if state.get("error"):
        parts.append(f'<p class="muted">承認済みの拡張を読み込めませんでした: {_escape(state["error"])}</p>')
        parts.append("</section>")
        return "".join(parts)

    if approved:
        parts.append("<h3>承認済み</h3><ol>")
        for entry in approved:
            patch = entry["patch"]
            lines = describe_add(patch.get("add") or {}, world)
            link_html = _experiment_link_html(entry.get("experiment"), run_link, "生まれた実験を見る →")
            parts.append(
                f'<li><strong>rev {_escape(entry.get("rev"))}: {_escape(patch.get("title"))}</strong>'
                "<ul>" + "".join(f"<li>{_escape(line)}</li>" for line in lines) + "</ul>"
                f'<p class="muted">承認理由: {_escape(entry.get("reason"))}</p>{link_html}</li>'
            )
        parts.append("</ol>")
        # R2 (viewer review): reopen() (execution/world_patch_approval.py)
        # actually restores *every* approved revision at once, not just the
        # last one -- later patches were gated against a world that already
        # had the earlier ones applied, so they can't be reopened alone. One
        # button, after the whole list, says so instead of implying a
        # per-item undo.
        if can_write:
            count = len(approved)
            parts.append(
                '<div class="we-reopen">'
                f'<p class="muted">戻すと、{count} 件とも検査のやり直しが必要になります。'
                "承認の記録は履歴に残ります。</p>"
                f'<button type="button" data-patch-action="reopen" data-world="{_escape(world_id)}" '
                f'data-head="{_escape(state.get("head"))}" data-count="{count}">'
                f"承認済みの拡張をすべて提案中へ戻す（{count}件）</button>"
                '<span class="we-message" data-patch-message role="alert"></span></div>'
            )
    else:
        parts.append("<p>承認済みの拡張はまだありません。</p>")

    if proposed:
        # V1 (viewer review): each proposal's title/status/trigger and a
        # link to the experiment that produced it -- previously just a bare
        # "N 件あります" count. No approve/reject here; that stays on the
        # experiment's own result screen (proposal_card).
        parts.append(f"<h3>提案中（{len(proposed)}件）</h3><ul>")
        for entry in proposed:
            patch = entry.get("patch")
            if not isinstance(patch, dict):
                parts.append(f'<li><strong>{_escape(entry.get("id"))}</strong>'
                             f'<p class="muted">{_escape(entry.get("error") or "読み込めない提案です")}</p></li>')
                continue
            gate = entry.get("gate") or {}
            status_html = _status_badge(gate.get("status", "trial_pending"))
            trigger = patch.get("trigger") or {}
            trigger_text = ""
            if trigger.get("zone") or trigger.get("verb"):
                trigger_text = f"きっかけ: {_escape(trigger.get('zone'))} で {_escape(trigger.get('verb'))}"
            link_html = _experiment_link_html(trigger.get("experiment"), run_link, "この実験の結果で確認する →")
            parts.append(
                f'<li><strong>{_escape(patch.get("title"))}</strong> {status_html}'
                + (f'<p class="muted">{trigger_text}</p>' if trigger_text else "")
                + link_html + "</li>"
            )
        parts.append("</ul>")

    if retired:
        # WB-WORLDGROW-001 段階5a: 淘汰済み（墓標リビジョン）の一覧。yaml/gate
        # は消えていない（読み直せば「何が増えるか」も出せるが、退場した拡張の
        # 詳細まではここでは出さない -- タイトル・理由・実験だけで十分）。
        parts.append(f"<h3>枯れた拡張（{len(retired)}件）</h3><ul>")
        for entry in retired:
            patch = entry["patch"]
            link_html = _experiment_link_html(entry.get("experiment"), run_link, "枯らした実験を見る →")
            parts.append(
                f'<li><strong>{_escape(patch.get("title"))}</strong>'
                f'<p class="muted">枯らした理由: {_escape(entry.get("reason"))}</p>{link_html}</li>'
            )
        parts.append("</ul>")

    parts.append("</section>")
    return "".join(parts)
