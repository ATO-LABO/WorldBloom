"""'世界の需要と拡張' の読み取り専用レンダリング (WB-WORLDGROW-001 段階2/3a).

viewer/pages.py に埋もれていた旧 experiment_page 本文からの移設。UI 刷新後は
viewer/run_workspace.py の観測画面の5つ目のタブと、viewer/sifting_pages.py の
サイドバーがここを呼ぶ。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from execution.provenance import ConfigError
from viewer import data, pages

_escape = pages._escape

_SAFE_ERRORS = (data.MissingResource, ConfigError, OSError, ValueError, KeyError, TypeError, AttributeError)


def resolve_root(repository: "data.RunRepository", run_name: str | None):
    """The experiment's real directory, or None.

    A job that is still running (or was never imported the legacy way) has no
    top-level archive.json -- its record lives under published/N/ -- which
    repository.experiment() refuses. So ask the catalog first, the way the
    run workspace's observation() does, and fall back for legacy runs.
    """
    if not run_name:
        return None
    if repository.catalog is not None:
        try:
            return repository.catalog.resolve(run_name)[0]
        except _SAFE_ERRORS:
            pass
    try:
        return repository.experiment(run_name)
    except _SAFE_ERRORS:
        return None


def expansion_line(patches: list) -> str:
    """1行サマリ + パッチごとの<li>（WB-WORLDGROW-001 段階3a）。パッチが無ければ
    「ベース（拡張なし）」の1行のみ。要素の型が違っても落ちない。

    ``patches`` は data.world_expansion_state() の戻り値
    ({"state": ..., "patches": [...]}) か、パッチの生リストのどちらでも良い。
    """

    if isinstance(patches, Mapping):
        if patches.get("state") == "unknown":
            return "<p>この実験の世界: 不明（拡張の情報が欠けています）</p>"
        patches = patches.get("patches", [])
    if not patches:
        return "<p>この実験の世界: ベース（拡張なし）</p>"

    kind_labels = (("zones", "ゾーン"), ("items", "アイテム"),
                   ("facts", "事実"), ("daily_events", "日々の出来事"),
                   ("sources", "入手手段"))
    items = []
    for patch in patches:
        if not isinstance(patch, Mapping):
            continue
        trigger = patch.get("trigger")
        zone = trigger.get("zone") if isinstance(trigger, Mapping) else None
        verb = trigger.get("verb") if isinstance(trigger, Mapping) else None
        # WB-WORLDGROW-002 S4: EXPANSION_TRIGGER_KEYS (gapengine/world_patch.py)
        # keeps a whiff trigger's slim record kind-less (byte-identical to
        # before S1) -- ignorance/blocked always carry "kind" -- so the
        # zone/verb branch below stays untouched for whiff.
        trigger_kind = trigger.get("kind") if isinstance(trigger, Mapping) else None
        if trigger_kind == "ignorance":
            trigger_text = f"きっかけ: {_escape(zone)} で手探り {_escape(trigger.get('count'))}回"
        elif trigger_kind == "blocked":
            # R3（段階4 review 1）: 「…手段が無く 91回」は手段が「無くなった」
            # ように読めるため、「…が無い状態 91回」に直す。
            trigger_text = (f"きっかけ: {_requirement_phrase(trigger.get('requirement'))}が無い状態 "
                             f"{_escape(trigger.get('count'))}回")
        elif zone and verb:
            trigger_text = f"きっかけ: {_escape(zone)} で {_escape(verb)}"
        elif zone or verb:
            trigger_text = f"きっかけ: {_escape(zone or verb)}"
        else:
            trigger_text = None

        added = patch.get("added")
        added_parts = []
        if isinstance(added, Mapping):
            for key, label in kind_labels:
                names = [n for n in data._as_list(added.get(key)) if isinstance(n, str)]
                if not names:
                    continue
                if key == "sources":
                    # R5（段階4 review 1): "入手手段 縄@森" は内部の "名前@場所"
                    # 表記のまま出ていた。既存の品/事実に手段が増えたことが
                    # 分かる文にする。
                    phrases = []
                    for n in names:
                        item_name, _, zone = n.rpartition("@")
                        phrases.append(f"「{_escape(item_name or n)}」を{_escape(zone)}で手に入れられるようにした"
                                       if item_name and zone else _escape(n))
                    added_parts.append("、".join(phrases))
                else:
                    added_parts.append(f"{label} " + "、".join(_escape(n) for n in names))
        added_text = "足したもの: " + "、".join(added_parts) if added_parts else None

        detail = " ／ ".join(part for part in (trigger_text, added_text) if part)
        suffix = f" — {detail}" if detail else ""
        items.append(
            f"<li><strong>{_escape(patch.get('title'))}</strong>"
            f"（{_escape(patch.get('id'))}）{suffix}</li>"
        )
    return "<p>この実験の世界: 拡張あり</p><ul>" + "".join(items) + "</ul>"


def summary_text(state: Any) -> str:
    """expansion_line と同じ判定（unknown/base/expanded）を再利用した短い1行。

    観測画面の「今回の条件」の世界行と Sifting サイドバーの件数表示が使う。
    """

    if not isinstance(state, Mapping):
        return "ベース（拡張なし）"
    if state.get("state") == "unknown":
        return "不明（拡張の情報が欠けています）"
    count = len(data._as_list(state.get("patches")))
    return f"拡張あり（{count}件）" if count else "ベース（拡張なし）"


def _own_rate_text(trigger: Mapping) -> str:
    """このきっかけ自身の 空振り/試行 の割合（分母が0なら空文字）。"""

    count = data._number(trigger.get("count"))
    if count <= 0:
        return ""
    whiffs = data._number(trigger.get("whiffs"))
    return f"（{whiffs / count * 100:.1f}%）"


_ROUTE_KIND_TEXT = (("advance", "前進"), ("prepare", "準備"))
_ROUTE_DETOUR_TEXT = (
    ("body", "身体"), ("belief", "思い込み"), ("ignorance", "手探り"),
    ("motive", "動機"), ("none", "理由なし"),
)


def _route_breakdown_text(counts: Any) -> str:
    """段階4: ゾーン表の道筋付き決定の内訳（前進・準備・寄り道〈内訳〉・見通し
    なし）。route_counts が無い/空のゾーン（道筋層を使っていない実験、または
    そのゾーンで route 付き決定が無い）は空文字。"""
    if not isinstance(counts, Mapping) or not counts:
        return ""
    parts = []
    for key, label in _ROUTE_KIND_TEXT:
        n = int(data._number(counts.get(key)))
        if n:
            parts.append(f"{label} {n}")
    detour_parts = []
    detour_total = 0
    for cause, label in _ROUTE_DETOUR_TEXT:
        n = int(data._number(counts.get(f"detour:{cause}")))
        if n:
            detour_total += n
            detour_parts.append(f"{label} {n}")
    if detour_total:
        parts.append(f"寄り道 {detour_total}（" + "、".join(detour_parts) + "）")
    lost = int(data._number(counts.get("lost")))
    if lost:
        parts.append(f"見通しなし {lost}")
    return "、".join(parts)


def _zone_ineffective_text(zone: Mapping) -> str:
    """ゾーンの空振り率を、試行回数（decisions）付きで表示する。

    件数が無い／0のときは元の値をそのまま表示する（壊れた入力でも落ちない）。
    """

    decisions = data._number(zone.get("decisions"))
    rate = zone.get("ineffective_rate")
    if decisions <= 0 or rate is None:
        return _escape(rate)
    return f"{data._number(rate) * 100:.1f}%（{decisions:.0f}回中）"


# WB-WORLDGROW-001 段階3b-3: the "拡張を提案させる" button per investigate
# trigger. Shown only when the caller (run_workspace._demand_html) has
# already confirmed the run can plausibly accept a proposal -- the final
# accept/reject is always the server's (POST .../world-patch can still 409/
# 422/503).
_PROPOSE_TIME_NOTE = (
    '<p class="muted">ローカルの文章生成モデルで1回の生成に6〜11分かかります。'
    "検査まで含めて10〜30分ほどです。実行中はほかの実行や生成を始められません。</p>"
)


def _propose_button(index: int, propose_run: str, label: str = "この場所の拡張を提案させる") -> str:
    return (
        ' <button type="button" data-patch-action="propose" '
        f'data-run="{_escape(propose_run)}" data-trigger="{index}">'
        f"{label}</button>"
    )


def _whiff_li(index: int, t: Mapping, propose_run: str | None) -> str:
    zone, verb = t.get("zone"), t.get("verb")
    body = (
        f'<strong>{_escape(zone)}</strong> で'
        f'「{_escape(verb)}」: '
        f'{_escape(t.get("count"))} 回中 {_escape(t.get("whiffs"))} 回が空振り{_own_rate_text(t)}'
        f'（全滞在決定の {data._number(t.get("wasted_share")) * 100:.1f}%）'
    )
    if propose_run:
        if verb == "investigate":
            body += _propose_button(index, propose_run)
        else:
            body += ' <span class="muted">（「investigate」＝調べる、の空振りにだけ拡張を提案できます）</span>'
    # data-trigger is the raw index into world_demand.json's triggers (every
    # kind/verb) -- since WB-WORLDGROW-002 S2 this is also exactly the
    # number `scripts/world_patch.py propose --trigger N` takes (no more
    # investigate-only re-numbering in between): execution/world_patch_job.
    # py's prepare() forwards this raw index unchanged into the argv.
    return f'<li data-trigger="{index}" data-zone="{_escape(zone)}" data-verb="{_escape(verb)}">{body}</li>'


def _zone_names(pairs: Any) -> str:
    """[[name, count], ...] の名前だけを「・」でつなぐ（段階4差し戻し対応と同じ
    「件数はゾーンの隣に付けない」方針。壊れた要素は無視、全滅なら「不明」）。
    R3（段階4 review 1）: 読点だと後続のラン数と混ざって読めるため「・」に変更。"""
    names = [pair[0] for pair in data._as_list(pairs)
             if isinstance(pair, (list, tuple)) and pair and isinstance(pair[0], str)]
    return "・".join(names) if names else "不明"


def _requirement_phrase(requirement: Any) -> str:
    """has_item:X/knows:F/reach:Z を利用者向けの読みやすい動詞句にする
    （守ること: 「has_item:縄」→「『縄』を手に入れる」）。"""
    if not isinstance(requirement, str):
        return "不明な入手手段"
    for prefix, verb in (("has_item:", "を手に入れる"), ("knows:", "を知る"), ("reach:", "に行く")):
        if requirement.startswith(prefix):
            return f"「{_escape(requirement[len(prefix):])}」{verb}手段"
    return f"「{_escape(requirement)}」を得る手段"


def _ignorance_li(index: int, t: Mapping, propose_run: str | None) -> str:
    zone = t.get("zone")
    share_pct = data._number(t.get("share")) * 100
    body = (
        f'<strong>{_escape(zone)}</strong> で手探り'
        f'（何をすべきか分からず調べ回る寄り道）が {_escape(t.get("count"))} 回'
        f"（道筋付き決定の {share_pct:.0f}%）"
    )
    if propose_run:
        body += _propose_button(index, propose_run)
    return f'<li data-trigger="{index}" data-zone="{_escape(zone)}" data-kind="ignorance">{body}</li>'


def _blocked_li(index: int, t: Mapping, propose_run: str | None) -> str:
    phrase = _requirement_phrase(t.get("requirement"))
    stuck_text = _zone_names(t.get("stuck_zones"))
    # R3（段階4 review 1）: 場所の並びとラン数を「。」で分け、ラン数が場所と
    # 混ざって読めないようにする（「道中、森、海、7 本のラン」→「道中・森・
    # 海で。7 本のランで」）。
    body = (
        f"{phrase}が無く、計画が立たない: {_escape(t.get('count'))} 回"
        f"（主に {_escape(stuck_text)}で。{_escape(t.get('runs'))} 本のランで）"
    )
    # R6（段階4 review 1、設計役の決定）: 判断材料として、入手できる場所・
    # 持っている人物を短く添える（データが無ければ何も出さない）。
    extras = []
    source_zones = [p[0] for p in data._as_list(t.get("source_zones"))
                     if isinstance(p, (list, tuple)) and p and isinstance(p[0], str)]
    if source_zones:
        extras.append(f"入手できる場所: {_escape('・'.join(source_zones))}")
    held_by = [p[0] for p in data._as_list(t.get("held_by"))
               if isinstance(p, (list, tuple)) and p and isinstance(p[0], str)]
    if held_by:
        extras.append(f"持っている人物: {_escape('・'.join(held_by))}")
    if extras:
        body += f' <span class="muted">（{"、".join(extras)}）</span>'
    if propose_run:
        # R2（段階4 review 1): blocked に「この場所」は無い（要件が対象。場所
        # は複数の場合がある）ため、入手手段の拡張という表現にする。
        body += _propose_button(index, propose_run, "入手手段の拡張を提案させる")
    return f'<li data-trigger="{index}" data-kind="blocked">{body}</li>'


def _trigger_li(index: int, t: Mapping, propose_run: str | None) -> str:
    kind = t.get("kind", "whiff")
    if kind == "ignorance":
        return _ignorance_li(index, t, propose_run)
    if kind == "blocked":
        return _blocked_li(index, t, propose_run)
    return _whiff_li(index, t, propose_run)


def demand_block(repository: "data.RunRepository", experiment: Any, state: Any = None, *,
                  propose_run: str | None = None) -> str:
    """世界の需要（WB-WORLDGROW-001 段階2/3a): この実験の世界（ベースか拡張ずみか）
    を常に1行で示し、world_expansion=detect/expand で回っていればゾーン別の
    空振りトリガーも平文で見せる。集計が無ければ案内文のみ。

    `propose_run`（実験名）を渡すと、investigate のきっかけごとに「拡張を提案
    させる」ボタンを足す（呼び出し元が提案を受け付けられる見込みを確認済みの
    ときだけ渡す想定 -- 段階3b-3）。"""

    # `state` lets a caller that already read world_expansion_state() pass it in.
    line = expansion_line(state if state is not None else data.world_expansion_state(repository, experiment))
    report = data.world_demand(repository, experiment)
    if report is None:
        return (
            '<section class="card"><h2>世界の需要と拡張</h2>'
            f"{line}"
            '<p class="muted">この実験は世界の需要を集計していません'
            '（実行設定の「世界の拡張」を「検知のみ」か「承認済みの拡張を適用」にして回した実験で出ます）。</p></section>'
        )

    # WB-WORLDGROW-002 S4: world_demand.collect() also returns "ignorance"/
    # "blocked" triggers (shaped differently -- no verb/whiffs) on a
    # route-wired run. Every kind is rendered now (_trigger_li dispatches by
    # kind) -- the raw index (which the propose button keys off) still
    # counts every entry, whiff or not, unchanged from before this stage.
    all_triggers = data._as_list(report.get("triggers"))
    renderable_triggers = [
        (index, t) for index, t in enumerate(all_triggers) if isinstance(t, Mapping)
    ]
    if renderable_triggers:
        rows = "".join(_trigger_li(index, t, propose_run) for index, t in renderable_triggers)
        # Only where a button actually appears -- the estimate alone would
        # hang in the air. whiff only gets a button on investigate; every
        # ignorance/blocked trigger in this list already cleared its own
        # threshold, so it's always proposable (gapengine.world_patch_propose.
        # trigger_is_proposable's own rule, kept in sync by eye here).
        can_propose = propose_run and any(
            t.get("kind", "whiff") in ("ignorance", "blocked") or t.get("verb") == "investigate"
            for _, t in renderable_triggers
        )
        note = _PROPOSE_TIME_NOTE if can_propose else ""
        trigger_html = f"{note}<ul>{rows}</ul>"
    else:
        trigger_html = "<p>拡張が必要そうな場所は見つかりませんでした。</p>"

    route_counts = report.get("route_counts")
    has_routes = isinstance(route_counts, Mapping) and bool(route_counts)
    route_th = "<th>道筋の内訳</th>" if has_routes else ""
    zone_rows = "".join(
        "<tr>"
        f"<td>{_escape(z.get('zone'))}</td>"
        f"<td>{_escape(z.get('dwell_share'))}</td>"
        f"<td>{_escape(z.get('repeat_rate'))}</td>"
        f"<td>{_zone_ineffective_text(z)}</td>"
        f"<td>{_escape('、'.join(f'{e[0]}×{e[1]}' for e in data._as_list(z.get('verbs')) if isinstance(e, list) and len(e) >= 2))}</td>"
        + (f"<td>{_escape(_route_breakdown_text(route_counts.get(z.get('zone'))))}</td>" if has_routes else "")
        + "</tr>"
        for z in data._as_list(report.get("zones"))
        if isinstance(z, Mapping)
    )
    zone_table = (
        "<details><summary>ゾーン別の詳細</summary>"
        '<table class="wb-table"><thead><tr>'
        f"<th>ゾーン</th><th>滞在シェア</th><th>繰り返し率</th><th>空振り率</th><th>主な行動</th>{route_th}"
        f"</tr></thead><tbody>{zone_rows}</tbody></table></details>"
    )

    # R4（段階4 review 1): この一文は空振り（whiff）専用の説明で、route層の
    # 手探り（ignorance）・計画が立たない（blocked）が混ざる実験ではずれる。
    # 種類が混ざるときだけ、それぞれの意味を足す。
    kinds_present = {t.get("kind", "whiff") for _, t in renderable_triggers}
    intro = ('<p class="muted">主人公がよく滞在するのに、行動が空振りしている場所です。'
             "世界の解像度が足りていない候補として読みます。</p>")
    if kinds_present - {"whiff"}:
        extra = []
        if "whiff" in kinds_present:
            extra.append("空振り＝よく滞在するのに行動が空振りしている場所")
        if "ignorance" in kinds_present:
            extra.append("手探り＝何をすべきか分からず調べ回っている場所")
        if "blocked" in kinds_present:
            extra.append("計画が立たない＝入手手段が無く先の計画が立てられない要件")
        intro = (
            '<p class="muted">主人公の行動から、世界の解像度が足りていない候補を'
            f"種類ごとに拾います（{'／'.join(extra)}）。</p>"
        )
    return (
        '<section class="card"><h2>世界の需要と拡張</h2>'
        f"{line}"
        f"{intro}"
        f"{trigger_html}{zone_table}</section>"
    )


def sidebar_link(repository: "data.RunRepository", run_name: str | None) -> str:
    """Sifting サイドバーの4本目のリンク。world_demand.json が無い実験では空文字
    （既存の3リンクのままで、既存テストが期待するHTMLを壊さない）。"""

    experiment = resolve_root(repository, run_name)
    if experiment is None:
        return ""
    try:
        report = data.world_demand(repository, experiment)
    except _SAFE_ERRORS:
        return ""
    if report is None:
        return ""
    # WB-WORLDGROW-002 S4: every kind (whiff/ignorance/blocked) is now shown
    # in demand_block, so the sidebar counts every kind too (S1 review 1's
    # whiff-only count was a stopgap until this stage rendered the others).
    all_triggers = data._as_list(report.get("triggers"))
    count = sum(1 for t in all_triggers if isinstance(t, Mapping))
    label = f"世界の需要（{count}件）" if count else "世界の需要"
    href = f"/exp/{pages._url_segment(run_name)}/monitor?tab=demand"
    return f'<a href="{_escape(href)}">{_escape(label)}</a>'
