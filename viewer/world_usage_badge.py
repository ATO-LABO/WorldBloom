"""段階4b「拡張要素を使った物語のバッジ」(WB-WORLDGROW-001)。

候補（物語）単位で、承認済みパッチ（凍結世界の expansion.patches）が実際に
使われたかを gapengine.world_patch_contract.new_usage で数える。保存しない
-- 凍結世界と不変のログから毎回導出する読み取り専用の純粋な計算。
ログの内容は同じパス・同じ更新時刻・同じサイズなら変わらない前提で、
(path, mtime_ns, size, 適用パッチの内容, protagonist) をキーにプロセス内
キャッシュする（viewer/data.py の _cached_explanation と同じ作法）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from gapengine.world_patch_contract import new_usage
from viewer import data, pages

_escape = pages._escape
_SAFE_ERRORS = (data.MissingResource, OSError, ValueError, KeyError, TypeError, AttributeError)

# new_usage() が数える6項目。daily_events は段階3で v1 パッチから除外済みなので対象外。
_LABELS = (
    ("decisions_in_new_zones", "新しい場所での決定"),
    ("moves_into_new_zones", "新しい場所への移動"),
    ("gathered_new_items", "新しい品を集めた"),
    ("gave_new_items", "新しい品を渡した"),
    ("learned_new_facts", "新しい事実を知った"),
    ("shared_new_facts", "新しい事実を話した"),
)


def _added_names(patches: Any) -> tuple[frozenset, frozenset, frozenset]:
    """expansion.patches[].added（名前/idだけの一覧）を、パッチ境界を問わず
    合算した3つの集合にする -- new_usage は所属の有無しか見ないため。"""
    zones, items, facts = set(), set(), set()
    for patch in patches or []:
        added = patch.get("added") if isinstance(patch, dict) else None
        if not isinstance(added, dict):
            continue
        zones.update(n for n in data._as_list(added.get("zones")) if isinstance(n, str))
        items.update(n for n in data._as_list(added.get("items")) if isinstance(n, str))
        facts.update(n for n in data._as_list(added.get("facts")) if isinstance(n, str))
    return frozenset(zones), frozenset(items), frozenset(facts)


def _patch_for(zones, items, facts) -> dict:
    return {"add": {"zones": [{"name": n} for n in zones],
                    "items": [{"name": n} for n in items],
                    "facts": [{"id": n} for n in facts]}}


@lru_cache(maxsize=256)
def _cached_counts(path, mtime_ns, size, zones, items, facts, protagonist):
    return new_usage([path], _patch_for(zones, items, facts), protagonist)


def usage_counts(repository, experiment, log_relative_path, protagonist, patches):
    """1候補分の使用回数、または対象外（拡張なし・ログ無し等）なら None。"""
    if not protagonist or not log_relative_path:
        return None
    zones, items, facts = _added_names(patches)
    if not (zones or items or facts):
        return None
    try:
        path = repository.safe_path(experiment, log_relative_path)
        if not path.is_file():
            return None
        stat = path.stat()
        # dict(...): _cached_counts の戻り値はlru_cache越しに全呼び出しで
        # 共有される同一オブジェクト。呼び出し側が書き換えても他へ波及しない
        # よう、返す前に浅いコピーを渡す（Opus review 推奨4）。
        return dict(_cached_counts(path, stat.st_mtime_ns, stat.st_size, zones, items, facts, protagonist))
    except _SAFE_ERRORS:
        return None


def _detail_text(counts) -> str:
    return "、".join(f"{label} {counts[key]}回" for key, label in _LABELS if counts.get(key))


def badge_html(counts) -> str:
    """2値（使った/使っていない）+ 詳細（<details>展開）で表示する。呼び出し側
    は <button> など対話的要素をネストできない親要素の外に置くこと
    （<details> は phrasing content ではない）。"""
    if counts is None:
        return ""
    if not any(counts.get(key) for key, _label in _LABELS):
        return '<span class="we-usage" data-used="false">拡張要素: 使っていない</span>'
    return (f'<details class="we-usage" data-used="true"><summary>拡張要素: 使った</summary>'
            f'<p class="muted">{_escape(_detail_text(counts))}</p></details>')


def cell_badge_html(repository, experiment, patches, elite, protagonist) -> str:
    """archive.json の cells[cell] 1件（exemplar.layers_path を持つ）向け。"""
    if not isinstance(elite, dict):
        return ""
    exemplar = elite.get("exemplar") or {}
    counts = usage_counts(repository, experiment, exemplar.get("layers_path"), protagonist, patches)
    return badge_html(counts)


def badge_for_run_candidate(repository, run_id, candidate_id) -> str:
    """上映（作品を読む）画面向け: catalogのrun_id + candidate_idから解決する。
    1候補だけなのでキャッシュの有無に関わらず軽い。"""
    if repository.catalog is None:
        return ""
    try:
        experiment, _legacy = repository.catalog.resolve(run_id)
        state = data.world_expansion_state(repository, experiment)
        if state.get("state") != "expanded":
            return ""
        config = data._read_json(repository.safe_path(experiment, "config.json"))
        protagonist = (config.get("preview") or {}).get("protagonist") if isinstance(config, dict) else None
        if not protagonist:
            return ""
        snapshot = repository.catalog.snapshot(run_id, observe=False)
        candidate = next((c for c in snapshot["candidates"]["candidates"] if c.get("candidate_id") == candidate_id), None)
        if candidate is None:
            return ""
        log = candidate.get("log") or {}
        if log.get("availability") != "present":
            return ""
        counts = usage_counts(repository, experiment, log.get("relative_path"), protagonist, state["patches"])
        return badge_html(counts)
    except _SAFE_ERRORS:
        return ""
