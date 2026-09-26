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
                   ("facts", "事実"), ("daily_events", "日々の出来事"))
    items = []
    for patch in patches:
        if not isinstance(patch, Mapping):
            continue
        trigger = patch.get("trigger")
        zone = trigger.get("zone") if isinstance(trigger, Mapping) else None
        verb = trigger.get("verb") if isinstance(trigger, Mapping) else None
        if zone and verb:
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
                if names:
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


def _trigger_li(index: int, t: Mapping, propose_run: str | None) -> str:
    zone, verb = t.get("zone"), t.get("verb")
    body = (
        f'<strong>{_escape(zone)}</strong> で'
        f'「{_escape(verb)}」: '
        f'{_escape(t.get("count"))} 回中 {_escape(t.get("whiffs"))} 回が空振り{_own_rate_text(t)}'
        f'（全滞在決定の {data._number(t.get("wasted_share")) * 100:.1f}%）'
    )
    if propose_run:
        if verb == "investigate":
            body += (
                ' <button type="button" data-patch-action="propose" '
                f'data-run="{_escape(propose_run)}" data-trigger="{index}">'
                "この場所の拡張を提案させる</button>"
            )
        else:
            body += ' <span class="muted">（「investigate」＝調べる、の空振りにだけ拡張を提案できます）</span>'
    # data-trigger is the raw index into world_demand.json's triggers (every
    # kind/verb) -- since WB-WORLDGROW-002 S2 this is also exactly the
    # number `scripts/world_patch.py propose --trigger N` takes (no more
    # investigate-only re-numbering in between): execution/world_patch_job.
    # py's prepare() forwards this raw index unchanged into the argv.
    return f'<li data-trigger="{index}" data-zone="{_escape(zone)}" data-verb="{_escape(verb)}">{body}</li>'


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

    # WB-WORLDGROW-002 S1: world_demand.collect() now also returns
    # "ignorance"/"blocked" triggers (shaped differently -- no verb/whiffs)
    # on a route-wired run. Rendering them is stage 2-4's own work
    # (gapengine/world_patch_propose.py etc.); until then this card skips
    # them by kind the same way it already skips a non-Mapping entry --
    # the raw index (which the propose button keys off) still counts every
    # entry, whiff or not.
    all_triggers = data._as_list(report.get("triggers"))
    whiff_triggers = [
        (index, t) for index, t in enumerate(all_triggers)
        if isinstance(t, Mapping) and t.get("kind", "whiff") == "whiff"
    ]
    if whiff_triggers:
        rows = "".join(_trigger_li(index, t, propose_run) for index, t in whiff_triggers)
        # Only where a button actually appears -- the estimate alone would hang in the air.
        can_propose = propose_run and any(t.get("verb") == "investigate" for _, t in whiff_triggers)
        note = _PROPOSE_TIME_NOTE if can_propose else ""
        trigger_html = f"{note}<ul>{rows}</ul>"
    else:
        trigger_html = "<p>拡張が必要そうな場所は見つかりませんでした。</p>"

    zone_rows = "".join(
        "<tr>"
        f"<td>{_escape(z.get('zone'))}</td>"
        f"<td>{_escape(z.get('dwell_share'))}</td>"
        f"<td>{_escape(z.get('repeat_rate'))}</td>"
        f"<td>{_zone_ineffective_text(z)}</td>"
        f"<td>{_escape('、'.join(f'{e[0]}×{e[1]}' for e in data._as_list(z.get('verbs')) if isinstance(e, list) and len(e) >= 2))}</td>"
        "</tr>"
        for z in data._as_list(report.get("zones"))
        if isinstance(z, Mapping)
    )
    zone_table = (
        "<details><summary>ゾーン別の詳細</summary>"
        '<table class="wb-table"><thead><tr>'
        "<th>ゾーン</th><th>滞在シェア</th><th>繰り返し率</th><th>空振り率</th><th>主な行動</th>"
        f"</tr></thead><tbody>{zone_rows}</tbody></table></details>"
    )

    return (
        '<section class="card"><h2>世界の需要と拡張</h2>'
        f"{line}"
        '<p class="muted">主人公がよく滞在するのに、行動が空振りしている場所です。'
        "世界の解像度が足りていない候補として読みます。</p>"
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
    # S1 review 1 recommended fix: only whiff triggers are actually shown
    # above (ignorance/blocked wait for stage 4's own display) -- counting
    # every kind here made the sidebar say "1件" while the body said "拡張
    # トリガーなし". Revert to counting every kind once stage 4 shows them.
    all_triggers = data._as_list(report.get("triggers"))
    count = sum(
        1 for t in all_triggers if isinstance(t, Mapping) and t.get("kind", "whiff") == "whiff"
    )
    label = f"世界の需要（{count}件）" if count else "世界の需要"
    href = f"/exp/{pages._url_segment(run_name)}/monitor?tab=demand"
    return f'<a href="{_escape(href)}">{_escape(label)}</a>'
