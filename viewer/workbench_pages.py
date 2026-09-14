"""HTML workbench: configuration forms, job monitoring, and Sifting (WB-UI-007).

Every state change goes through the existing JSON APIs (job_api.py,
run_catalog.py); this module only renders HTML and adds the one missing
HTTP route (duplicate-save). GA execution never touches an LLM.
"""
from __future__ import annotations

from http import HTTPStatus
import json
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from execution.configs import evolution_defaults
from execution.provenance import ConfigError, contained
from execution.worker import TERMINAL
from gapengine.synopsis import BACKENDS
from viewer import data, job_api, pages


_escape = pages._escape
_url = pages._url_segment

RUNNING_STATES = frozenset({"queued", "running", "stopping"})

STATE_LABELS = {
    "queued": "待機中",
    "running": "実行中",
    "stopping": "停止処理中",
    "succeeded": "完了",
    "partial": "一部完了",
    "failed": "失敗",
    "cancelled": "停止済み",
    "interrupted": "中断（プロセス消失）",
    "legacy": "旧実験",
    "untracked": "台帳外",
}

PHASE_LABELS = {
    "preparing": "準備中",
    "evaluating": "評価中",
    "publishing": "世代確定中",
    "generation_completed": "世代完了",
    "generating": "生成中",
}

ERROR_MESSAGES = {
    "worker_disappeared": ("監視プロセスが消失しました", "同じ設定で新しく実行できます"),
    "launch_unconfirmed": ("監視プロセスの起動を確認できませんでした", "同じ設定で新しく実行できます"),
    "launch_failed": ("監視プロセスを起動できませんでした", "実行環境を確認してから新しく実行してください"),
    "wall_timeout": ("実行時間の上限に達しました", "上限を見直した新しい設定版で実行してください"),
    "preparation_failed": ("入力とコードの固定に失敗しました", "設定の入力を確認してください"),
    "process_failed": ("GAプロセスが異常終了しました", "実行ログを確認してから新しく実行してください"),
}

CANDIDATE_STATE_OPTIONS = ("adopted", "held", "rejected", "unclassified")
AVAILABILITY_LABELS = {"present": "あり", "pruned": "剪定済み", "missing": "不在", "stale": "不一致"}

# §6: candidate-list column sort. "state" here is the *selection* state (選定
#状態 column), not the run/job state; "draft" is the 稿 column's ok count
# (synopsize + narrate).
SORT_KEYS = ("generation", "individual_index", "seed", "cell_key", "reached", "quality", "state", "draft")
_SORT_LABELS = {
    "generation": "世代", "individual_index": "個体", "seed": "seed", "cell_key": "セル",
    "reached": "到達", "quality": "q", "state": "選定状態", "draft": "稿",
}

# Embedded verbatim into /jobs/{id} as data-* JSON so workbench.js polls against
# the same vocabulary the server rendered with, instead of hand-copying it.
TERMINAL_JSON = json.dumps(sorted(TERMINAL), ensure_ascii=False)
STATE_LABELS_JSON = json.dumps(STATE_LABELS, ensure_ascii=False, sort_keys=True)
PHASE_LABELS_JSON = json.dumps(PHASE_LABELS, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------
# Small render helpers
# --------------------------------------------------------------------------

def state_badge(state):
    label = STATE_LABELS.get(state, str(state))
    # data-field="state" lives on this element itself (not a wrapping <p>) so
    # polling JS can swap both its className and its label span uniquely.
    return (
        f'<span class="state-badge state-{_escape(state)}" data-field="state">'
        f'<span data-field="state-label">{_escape(label)}</span></span>'
    )


def _dl(pairs):
    items = "".join(f"<dt>{_escape(label)}</dt><dd>{value}</dd>" for label, value in pairs)
    return f'<dl class="metric">{items}</dl>'


def _short_id(value):
    text = str(value)
    return text[:12] + "…" if len(text) > 12 else text


def _th_title(term_key):
    return _escape(pages.TERM_HELP[term_key])


def _guidance_page(title="実行管理", *, phase=None):
    body = (
        '<section class="card"><p>実行管理は未設定です。'
        '<code>--control &lt;管理フォルダ&gt;</code> を付けてビューアを起動してください。</p></section>'
    )
    return pages.document(title, body, phase=phase)


def _query(handler):
    return parse_qs(urlsplit(handler.path).query, keep_blank_values=True)


def _clean_query(query):
    """Drop keys whose only value is empty.

    The filter form (§3.8) always submits every field, blank or not
    (`generation=&reached=true&...`); run_catalog.candidates()'s filter
    parsing treats a present-but-empty value as an invalid one, so an
    all-fields-rendered GET would 422 on the very first load.
    """

    return {key: values for key, values in query.items() if values and values[0] != ""}


def _elapsed_seconds(job, progress):
    explicit = progress.get("elapsed_seconds")
    if explicit is not None:
        return explicit
    start = job.get("started_at") or job.get("created_at")
    if start is None:
        return None
    end = job.get("finished_at") or time.time()
    try:
        return round(end - start, 1)
    except TypeError:
        return None


def _job_store(handler):
    return getattr(handler.server, "job_store", None)


# --------------------------------------------------------------------------
# Configuration form fields
# --------------------------------------------------------------------------

def _text_field(label, name, value, *, required=False):
    req = " required" if required else ""
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">{_escape(label)}</label>'
        f'<input id="f-{_escape(name)}" type="text" name="{_escape(name)}" '
        f'data-field="{_escape(name)}" value="{_escape(value)}"{req}>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        "</div>"
    )


def _number_field(label, name, value):
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">{_escape(label)}</label>'
        f'<input id="f-{_escape(name)}" type="number" step="1" name="{_escape(name)}" '
        f'data-field="{_escape(name)}" value="{_escape(value)}">'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        "</div>"
    )


def _select_field(label, name, options, selected):
    opts = "".join(
        f'<option value="{_escape(option)}"{" selected" if option == selected else ""}>{_escape(option)}</option>'
        for option in options
    )
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">{_escape(label)}</label>'
        f'<select id="f-{_escape(name)}" name="{_escape(name)}" data-field="{_escape(name)}">{opts}</select>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        "</div>"
    )


def _checkbox_field(label, name, checked):
    chk = " checked" if checked else ""
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">'
        f'<input id="f-{_escape(name)}" type="checkbox" name="{_escape(name)}" '
        f'data-field="{_escape(name)}"{chk}> {_escape(label)}</label>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        "</div>"
    )


def _initial_values(*, label, project_id, template_id, evolution, execution_limits, generation):
    values = {"label": label, "project_id": project_id, "template_id": template_id}
    for key in ("generations", "population", "seeds", "seed_base", "ga_seed", "processes"):
        values[f"evolution.{key}"] = evolution[key]
    values["evolution.keep"] = evolution["keep"]
    for key in ("coevolve", "meta_evolution", "record_explanations"):
        values[f"evolution.{key}"] = evolution[key]
    values["evolution.target_ending"] = (
        ", ".join(evolution["target_ending"]) if evolution.get("target_ending") else ""
    )
    values["execution_limits.wall_seconds"] = execution_limits["wall_seconds"]
    values["generation.backend"] = generation["backend"]
    values["generation.model"] = generation.get("model") or ""
    limits = generation.get("limits") or {}
    for key in ("max_calls", "call_timeout_seconds", "wall_seconds", "max_saved_response_bytes"):
        values[f"generation.limits.{key}"] = limits.get(key, "")
    return values


def _new_config_values():
    defaults = evolution_defaults()
    return _initial_values(
        label="", project_id="", template_id="",
        evolution=defaults,
        execution_limits={"wall_seconds": 3600},
        generation={
            "backend": "codex-cli", "model": None,
            "limits": {"max_calls": 1, "call_timeout_seconds": 180,
                       "wall_seconds": 240, "max_saved_response_bytes": 128000},
        },
    )


def render_config_form(values, *, projects, templates, backends, parent_config_id=None):
    duplicate = parent_config_id is not None
    if duplicate:
        # No data-field here: project_id/template_id are fixed by the parent
        # config and must never be sent inside the duplicate API's "changes".
        project_block = (
            f'<input type="hidden" name="project_id" value="{_escape(values["project_id"])}">'
            f'<p class="muted">世界: {_escape(values["project_id"])}（複製元と同じ）</p>'
        )
        template_block = (
            f'<input type="hidden" name="template_id" value="{_escape(values["template_id"])}">'
            f'<p class="muted">ジャンル: {_escape(values["template_id"])}（複製元と同じ）</p>'
        )
    else:
        project_block = _select_field("世界", "project_id", projects, values["project_id"])
        template_block = _select_field("ジャンル", "template_id", templates, values["template_id"])
    fields = (
        _text_field("設定名", "label", values["label"], required=True)
        + project_block + template_block
        + _number_field("世代数", "evolution.generations", values["evolution.generations"])
        + _number_field("個体数", "evolution.population", values["evolution.population"])
        + _number_field("seed数", "evolution.seeds", values["evolution.seeds"])
        + _number_field("seed_base", "evolution.seed_base", values["evolution.seed_base"])
        + _number_field("ga_seed", "evolution.ga_seed", values["evolution.ga_seed"])
        + _number_field("processes", "evolution.processes", values["evolution.processes"])
        + _select_field("保存方針", "evolution.keep", ("all", "reached", "exemplar"), values["evolution.keep"])
        + _checkbox_field("共進化", "evolution.coevolve", values["evolution.coevolve"])
        + _checkbox_field("メタ進化", "evolution.meta_evolution", values["evolution.meta_evolution"])
        + _checkbox_field("説明記録", "evolution.record_explanations", values["evolution.record_explanations"])
        + _text_field("結末（カンマ区切り。空なら世界の既定）", "evolution.target_ending", values["evolution.target_ending"])
        + _number_field("実行時間上限（秒）", "execution_limits.wall_seconds", values["execution_limits.wall_seconds"])
        + _select_field("生成方式", "generation.backend", backends, values["generation.backend"])
        + _text_field("モデル（空なら未指定）", "generation.model", values["generation.model"])
        + _number_field("max_calls", "generation.limits.max_calls", values["generation.limits.max_calls"])
        + _number_field("call_timeout_seconds", "generation.limits.call_timeout_seconds",
                         values["generation.limits.call_timeout_seconds"])
        + _number_field("生成側 wall_seconds", "generation.limits.wall_seconds",
                         values["generation.limits.wall_seconds"])
        + _number_field("max_saved_response_bytes", "generation.limits.max_saved_response_bytes",
                         values["generation.limits.max_saved_response_bytes"])
    )
    preview_button = "" if duplicate else '<button type="button" data-action="preview">検証する</button>'
    save_label = "複製として保存" if duplicate else "新しい版として保存"
    parent_attr = f' data-parent="{_escape(parent_config_id)}"' if duplicate else ""
    return (
        f'<form data-wb="config-form"{parent_attr}>'
        '<p class="form-error" data-form-error role="alert"></p>'
        f'<div class="form-grid">{fields}</div>'
        f"{preview_button}"
        f'<button type="submit">{_escape(save_label)}</button>'
        '<div data-preview></div>'
        "</form>"
    )


def render_configs_list(configs):
    if not configs:
        table = "<p>設定がありません。</p>"
    else:
        rows = "".join(
            "<tr>"
            f'<td><a href="/configs/{_url(c["config_id"])}">{_escape(c["label"])}</a></td>'
            f'<td>{_escape(c["config_id"])}</td>'
            f'<td>{_escape(c["project_id"])} / {_escape(c["template_id"])}</td>'
            f'<td>{_escape(c["evolution"]["generations"])}×'
            f'{_escape(c["evolution"]["population"])}×{_escape(c["evolution"]["seeds"])}</td>'
            f'<td>{_escape(c["created_at"])}</td>'
            "<td>"
            + (
                f'<a href="/configs/{_url(c["parent_config_id"])}">{_escape(c["parent_config_id"])}</a>'
                if c.get("parent_config_id") else "—"
            )
            + "</td>"
            '<td class="wb-actions">'
            f'<a href="/configs/{_url(c["config_id"])}">確認</a>'
            f'<a href="/configs/{_url(c["config_id"])}/start">GAを実行</a>'
            "</td></tr>"
            for c in configs
        )
        table = (
            '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
            f'<th>設定名</th><th title="{_th_title("config_id")}">config_id</th>'
            f'<th title="{_th_title("genre")}">ジャンル</th><th>世代×個体×seed</th>'
            "<th>作成日時</th><th>複製元</th><th>操作</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    # No .next-cta class here: the page-level next_action (set by the caller,
    # WB-UI-012 §2.2's "/configs" row) already is this same link.
    return '<section class="card"><h2>実行設定</h2>' + table + "</section>"


def render_config_detail(config):
    preview = config["preview"]
    ev = config["evolution"]
    parent = config.get("parent_config_id")
    parent_block = (
        f'<p><a href="/configs/{_url(parent)}">複製元: {_escape(parent)}</a></p>' if parent else ""
    )
    world_section = _dl([
        ("世界", _escape(preview["world_name"])),
        ("主人公", _escape(preview["protagonist"])),
        ("敵役", _escape(preview["antagonist"])),
        ("解決済み結末", _escape(", ".join(preview["target_endings"]))),
        ("最大ターン", _escape(preview["max_turns"])),
    ])
    ending_text = ", ".join(ev["target_ending"]) if ev.get("target_ending") else "世界の既定"
    ga_section = _dl([
        ("世代数", _escape(ev["generations"])),
        ("個体数", _escape(ev["population"])),
        ("seed数", _escape(ev["seeds"])),
        ("seed_base", _escape(ev["seed_base"])),
        ("ga_seed", _escape(ev["ga_seed"])),
        ("processes", _escape(ev["processes"])),
        ("保存方針", _escape(ev["keep"])),
        ("共進化", _escape(ev["coevolve"])),
        ("メタ進化", _escape(ev["meta_evolution"])),
        ("説明記録", _escape(ev["record_explanations"])),
        ("結末", _escape(ending_text)),
        ("実行時間上限（秒）", _escape(config["execution_limits"]["wall_seconds"])),
    ]) + '<p class="muted">列の値は次の版で変更可</p>'
    fixed = preview["fixed_parameters"]
    fixed_section = _dl([
        ("由来", _escape(fixed["source"])),
        ("mutation_probability", _escape(fixed["mutation_probability"])),
        ("rule_mutation_probability", _escape(fixed["rule_mutation_probability"])),
    ]) + '<p class="muted">実装上固定の値のため編集不可</p>'
    planned_section = _dl([
        ("個体評価数（予定）", _escape(preview["planned_individual_evaluations"])),
        ("seed評価数（予定）", _escape(preview["planned_seed_evaluations"])),
        ("seed範囲", f'{_escape(preview["seed_range"]["first"])} から {_escape(preview["seed_range"]["count"])} 件'),
    ]) + ('<p class="muted">共進化のため2倍</p>' if ev["coevolve"] else "")
    fallbacks = preview.get("fallbacks") or {}
    if fallbacks:
        fallback_section = "<ul>" + "".join(
            f"<li>{_escape(name)}: {_escape(info.get('reason'))}</li>"
            for name, info in fallbacks.items()
        ) + "</ul>"
    else:
        fallback_section = "<p>省略なし</p>"
    gen = config["generation"]
    gen_preview = preview["generation"]
    gen_pairs = [
        ("方式", _escape(gen["backend"])),
        ("モデル", _escape(gen["model"]) if gen.get("model") else "未指定"),
        ("max_calls", _escape(gen["limits"]["max_calls"])),
        ("call_timeout_seconds", _escape(gen["limits"]["call_timeout_seconds"])),
        ("wall_seconds", _escape(gen["limits"]["wall_seconds"])),
        ("max_saved_response_bytes", _escape(gen["limits"]["max_saved_response_bytes"])),
        ("可否（保存時点）", _escape(gen_preview.get("available"))),
        ("認証", _escape(gen_preview.get("authentication"))),
    ]
    completion_kind = gen_preview.get("completion_kind")
    if completion_kind:
        gen_pairs.append(("完了種別", _escape(completion_kind)))
    gen_section = _dl(gen_pairs) + '<p class="muted">保存時点の可否。認証の有効性は未確認</p>'
    provenance_section = _dl([
        ("作成日時", _escape(config["created_at"])),
        ("input_manifest_sha256", _escape(config["input_manifest_sha256"])),
    ])
    actions = (
        '<p class="actions">'
        f'<a href="/configs/{_url(config["config_id"])}/start">この設定でGAを実行</a>'
        f'<a href="/configs/new?from={_url(config["config_id"])}">複製して編集</a>'
        '<a href="/configs">一覧へ</a>'
        "</p>"
    )
    return (
        f'<p class="muted">config_id: {_escape(config["config_id"])}</p>'
        + parent_block
        + '<section class="card"><h2>世界と人物</h2>' + world_section + "</section>"
        + '<section class="card"><h2>GA設定</h2>' + ga_section + "</section>"
        + '<section class="card"><h2>実装上固定の値</h2>' + fixed_section + "</section>"
        + '<section class="card"><h2>予定評価数</h2>' + planned_section + "</section>"
        + '<section class="card"><h2>省略ファイルと実効値</h2>' + fallback_section + "</section>"
        + '<section class="card"><h2>生成設定</h2>' + gen_section + "</section>"
        + '<section class="card"><h2>来歴</h2>' + provenance_section + "</section>"
        + actions
    )


def render_start_confirm(config, request_id):
    ev = config["evolution"]
    preview = config["preview"]
    ending_text = ", ".join(ev["target_ending"]) if ev.get("target_ending") else "世界の既定"
    summary = _dl([
        ("終了条件（世代数）", _escape(ev["generations"])),
        ("個体数", _escape(ev["population"])),
        ("seed数", _escape(ev["seeds"])),
        ("seed_base", _escape(ev["seed_base"])),
        ("ga_seed", _escape(ev["ga_seed"])),
        ("processes", _escape(ev["processes"])),
        ("保存方針", _escape(ev["keep"])),
        ("共進化", _escape(ev["coevolve"])),
        ("メタ進化", _escape(ev["meta_evolution"])),
        ("説明記録", _escape(ev["record_explanations"])),
        ("結末", _escape(ending_text)),
        ("個体評価数（予定）", _escape(preview["planned_individual_evaluations"])),
        ("seed評価数（予定）", _escape(preview["planned_seed_evaluations"])),
        ("実行時間上限（秒）", _escape(config["execution_limits"]["wall_seconds"])),
    ])
    return (
        f'<div data-wb="start" data-config-id="{_escape(config["config_id"])}" '
        f'data-request-id="{_escape(request_id)}">'
        f'<h2>{_escape(config["label"])} を実行</h2>'
        + summary
        + '<p class="muted">GA は LLM を呼び出しません。</p>'
        + '<p class="form-error" data-form-error role="alert"></p>'
        + '<form><button type="submit">この設定でGAを実行</button></form>'
        + f'<p class="actions"><a href="/configs/{_url(config["config_id"])}">設定に戻る</a></p>'
        + "</div>"
    )


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------

def _job_row(record):
    name_cell = _escape(record["experiment_name"])
    if record["experiment_name"] != record["run_id"]:
        name_cell += f' <span class="muted">({_escape(record["run_id"])})</span>'
    revision = record.get("publication_revision")
    cid = record.get("config_id")
    config_cell = f'<a href="/configs/{_url(cid)}">{_escape(cid)}</a>' if cid else "未記録"
    actions = []
    if record.get("job_id"):
        actions.append(f'<a href="/jobs/{_url(record["job_id"])}">処理画面</a>')
    if record["state"] == "legacy" or revision is not None:
        actions.append(f'<a href="/exp/{_url(record["experiment_name"])}">結果</a>')
        actions.append(f'<a href="/runs/{_url(record["run_id"])}/candidates">候補一覧</a>')
    return (
        "<tr>"
        f"<td>{name_cell}</td>"
        f"<td>{state_badge(record['state'])}</td>"
        f"<td>{_escape(PHASE_LABELS.get(record.get('phase'), record.get('phase')))}</td>"
        f"<td>{_escape(revision) if revision is not None else '—'}</td>"
        f"<td>{config_cell}</td>"
        f'<td class="wb-actions">{" ".join(actions)}</td>'
        "</tr>"
    )


def _target_card(job, config):
    world = _config_world(config)
    label_link = f'<a href="/configs/{_url(config["config_id"])}">{_escape(config["label"])}</a>'
    if job is not None:
        verb = "を GA で処理中" if job.get("kind", "evolve") == "evolve" else "の作品を生成中"
        heading = f'{_escape(world["name"])} {verb}'
        rows = [("設定", label_link), ("状態", state_badge(job["state"]))]
        cta = ("進捗を見る →", f'/jobs/{_url(job["job_id"])}') if job.get("job_id") else None
    else:
        heading = f'{_escape(world["name"])} を GA で処理します'
        rows = [("設定", label_link)]
        cta = ("この設定でGAを実行 →", f'/configs/{_url(config["config_id"])}/start')
    cta_html = ""
    if cta:
        cta_label, cta_href = cta
        cta_html = f'<p><a class="button primary" href="{cta_href}">{_escape(cta_label)}</a></p>'
    return f'<section class="card target-card"><h2>{heading}</h2>{_dl(rows)}{cta_html}</section>'


def render_current_target_section(pin):
    return _target_card(pin["job"], pin["config"]) if pin else ""


def render_jobs_list(records):
    running = [r for r in records if r["state"] in RUNNING_STATES]
    history = [r for r in records if r["state"] not in RUNNING_STATES]

    def table(rows, empty_text):
        if not rows:
            return f"<p>{empty_text}</p>"
        body = "".join(_job_row(r) for r in rows)
        return (
            '<div class="grid-wrap"><table class="wb-table wb-table--compact"><thead><tr>'
            f'<th title="{_th_title("run")}">実験</th>'
            f'<th title="{_th_title("job_state")}">状態</th>'
            f'<th title="{_th_title("phase")}">段階</th>'
            f'<th title="{_th_title("publication_revision")}">公開版</th>'
            f'<th title="{_th_title("config_id")}">設定</th>'
            "<th>操作</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div>"
        )

    return (
        "<section><h2>進行中</h2>" + table(running, "進行中の実行はありません") + "</section>"
        "<section><h2>履歴</h2>" + table(history, "実行履歴はありません") + "</section>"
    )


def _connection_warnings(reconciliation):
    """The connection-lost and reconciliation-pending notices.

    Order matters here for UI-007 parity (state badge -> phase -> these two),
    so callers splice this in right after their own kind-specific first line
    (e.g. the phase paragraph) rather than _job_shell forcing a fixed slot.
    """
    return [
        '<p class="warning" data-connection-status hidden>'
        "サーバーに接続できません（再試行中）</p>",
        # Always rendered (toggled with the `hidden` attribute) so polling JS
        # can show/hide it without needing to create the element on the fly.
        '<p class="warning" data-field="reconciliation"'
        + ("" if reconciliation == "unknown" else " hidden")
        + ">状態確認中: 監視プロセスの生存を確認しています。"
        "確認できるまで新しい実行は受け付けられません</p>",
    ]


def _job_shell(job, body_parts, *, extra_attrs=""):
    """Common processing-page chrome shared by GA jobs (render_job_page) and
    generation jobs (output_pages.render_generation_job): state badge, cancel
    button and tail links. Only the kind-specific body (including where it
    places _connection_warnings()) differs between the two. `extra_attrs` lets
    a caller add its own data-* attributes to the root <section> (e.g. the
    generation job page's data-entry-labels for translating counts in JS).
    """
    state = job.get("state")
    terminal = state in TERMINAL
    parts = [
        f'<section data-wb="job" data-job-id="{_escape(job.get("job_id"))}" data-poll="1" '
        f'data-terminal="{"true" if terminal else "false"}" '
        f'data-terminal-states="{_escape(TERMINAL_JSON)}" '
        f'data-state-labels="{_escape(STATE_LABELS_JSON)}" '
        f'data-phase-labels="{_escape(PHASE_LABELS_JSON)}"{extra_attrs}>',
        f'<p>{state_badge(state)} '
        f'<span data-field="updated-at" class="muted"></span> '
        f'<span data-field="delta" class="muted"></span></p>',
    ]
    parts.extend(body_parts)
    # §7: static placeholder -- workbench.js's applyJob() fills this in on the
    # first poll (etaText()), so no server-side ETA computation is needed here.
    parts.append('<p>完了予定 <span data-field="eta">—</span></p>')
    if not terminal:
        disabled = " disabled" if state == "stopping" else ""
        label = "停止処理中（猶予後に強制終了）" if state == "stopping" else "停止"
        parts.append(
            f'<button type="button" data-action="cancel"{disabled}>{_escape(label)}</button>'
            '<span data-cancel-status></span>'
        )
    tail_links = ['<a href="/jobs">実行履歴へ</a>']
    config_id = job.get("config_id")
    if config_id:
        tail_links.append(f'<a href="/configs/{_url(config_id)}">設定</a>')
    parts.append(f'<p class="actions">{"".join(tail_links)}</p>')
    parts.append("</section>")
    return "".join(parts)


def render_job_page(job):
    state = job.get("state")
    terminal = state in TERMINAL
    progress = job.get("progress") or {}
    parts = [
        f'<p data-field="phase">{_escape(PHASE_LABELS.get(job.get("phase"), job.get("phase")))}</p>',
    ]
    parts.extend(_connection_warnings(job.get("reconciliation")))
    total_generations = progress.get("total_generations")
    completed_generations = progress.get("completed_generations")
    if total_generations:
        parts.append(
            '<div class="progress-row"><label>完了世代 '
            f'<span data-field="completed_generations">{_escape(completed_generations)}</span>/'
            f'<span data-field="total_generations">{_escape(total_generations)}</span></label>'
            f'<progress aria-label="完了世代" value="{int(completed_generations or 0)}" '
            f'max="{int(total_generations)}"></progress></div>'
        )
    total_individuals = progress.get("total_individuals")
    completed_individuals = progress.get("completed_individuals")
    parts.append(
        '<div class="progress-row"><label>評価済み個体 '
        f'<span data-field="completed_individuals">{_escape(completed_individuals)}</span>/'
        f'<span data-field="total_individuals">{_escape(total_individuals)}</span></label>'
        f'<progress aria-label="評価済み個体" value="{int(completed_individuals or 0)}" '
        f'max="{int(total_individuals or 1)}"></progress></div>'
    )
    parts.append(
        '<p>評価済みseed '
        f'<span data-field="completed_seeds">{_escape(progress.get("completed_seeds"))}</span>/'
        f'<span data-field="total_seeds">{_escape(progress.get("total_seeds"))}</span></p>'
    )
    active_seeds = progress.get("active_seeds")
    if active_seeds is not None:
        note = "未完了seedの記録（生存プロセス数ではない）" if terminal else "実行中seed数"
        parts.append(f'<p>{_escape(note)}: <span data-field="active_seeds">{len(active_seeds)}</span></p>')
    elapsed = _elapsed_seconds(job, progress)
    parts.append(
        '<p>経過秒: '
        f'<span data-field="elapsed_seconds">{_escape(elapsed) if elapsed is not None else ""}</span></p>'
    )
    revision = job.get("publication_revision")
    parts.append(
        '<p>公開版: '
        f'<span data-field="publication_revision">{_escape(revision) if revision is not None else "—"}</span></p>'
    )
    parts.append(f'<p>開始: {_escape(job.get("started_at"))} / 終了: {_escape(job.get("finished_at"))}</p>')
    by_role = progress.get("by_role")
    if by_role:
        rows = "".join(
            f"<tr><td>{_escape(role)}</td>"
            f"<td>{_escape(values.get('completed_individuals'))}/{_escape(values.get('total_individuals'))}</td>"
            f"<td>{_escape(values.get('completed_seeds'))}/{_escape(values.get('total_seeds'))}</td></tr>"
            for role, values in by_role.items()
        )
        parts.append(
            '<div class="grid-wrap"><table class="wb-table"><thead><tr><th>役割</th><th>個体</th><th>seed</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
        )
    config_id = job.get("config_id")
    run_id = job.get("run_id")
    if state in ("failed", "cancelled", "interrupted"):
        code = (job.get("error") or {}).get("code")
        if code is None and state == "cancelled":
            # A user-requested stop is not an error; say so instead of "エラー: None".
            parts.append('<p class="warning">利用者の停止要求により停止しました</p>'
                         '<p>同じ設定で新しく実行できます</p>')
        else:
            message, next_step = ERROR_MESSAGES.get(
                code, ("エラー情報がありません" if code is None else f"エラー: {code}", ""))
            parts.append(f'<p class="error">{_escape(message)}</p><p>{_escape(next_step)}</p>')
        if config_id:
            parts.append(f'<p><a href="/configs/{_url(config_id)}/start">同じ設定で新しく実行</a></p>')
        if job.get("publication_revision") is not None:
            parts.append(
                '<p class="actions">'
                f'<a href="/exp/{_url(run_id)}">途中まで確定した結果</a>'
                f'<a href="/runs/{_url(run_id)}/candidates">候補一覧</a></p>'
            )
    if state in ("succeeded", "partial"):
        parts.append(
            '<p class="actions">'
            f'<a href="/exp/{_url(run_id)}">確定結果を見る</a>'
            f'<a href="/runs/{_url(run_id)}/candidates">候補一覧</a></p>'
        )
    return _job_shell(job, parts)


# --------------------------------------------------------------------------
# Candidates / Sifting
# --------------------------------------------------------------------------

def _parse_filters(query):
    """Mirror run_catalog.dispatch's GET-candidates query parsing exactly."""

    filters = {}
    state = query.pop("state", None)
    if state is not None and (len(state) != 1 or state[0] not in CANDIDATE_STATE_OPTIONS):
        raise ConfigError("state", "選定状態が正しくありません")
    for key, values in query.items():
        if len(values) != 1:
            raise ConfigError("filters", "絞り込み値は1つ指定してください")
        value = values[0]
        if key in ("generation", "individual_index", "seed"):
            if not value.isascii() or not value.isdigit():
                raise ConfigError(key, "0以上の整数で指定してください")
            value = int(value)
        elif key == "reached":
            if value not in ("true", "false"):
                raise ConfigError(key, "trueまたはfalseで指定してください")
            value = value == "true"
        filters[key] = value
    return filters, (state[0] if state else None)


def _reached_text(value):
    if value is True:
        return "到達"
    if value is False:
        return "未到達"
    return "不明"


class _Desc:
    """Wrap a value so tuple comparison sorts it in reverse.

    Used only for the *present* half of a sort key (see sort_candidates): the
    "value is None" half stays unwrapped so None always sorts last regardless
    of direction, instead of jumping to the front under a naive reverse=True.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return self.value == other.value

    def __lt__(self, other):
        return other.value < self.value


def _sort_value(candidate, key, output_summary):
    if key == "draft":
        counts = (output_summary or {}).get(candidate["candidate_id"], {})
        return counts.get("synopsize", 0) + counts.get("narrate", 0)
    if key == "reached":
        value = candidate.get("reached")
        return None if value is None else (1 if value else 0)
    return candidate.get(key)


def sort_candidates(candidates, key, direction, output_summary):
    """Stable sort by one column; None sorts last, ties break by candidate_id."""

    def sort_key(candidate):
        value = _sort_value(candidate, key, output_summary)
        wrapped = _Desc(value) if direction == "desc" and value is not None else value
        return (value is None, wrapped, candidate["candidate_id"])

    return sorted(candidates, key=sort_key)


def _sort_th(label, column, *, query, active_sort, active_dir, term_key=None):
    title_attr = f' title="{_th_title(term_key)}"' if term_key else ""
    if column not in SORT_KEYS:
        return f"<th{title_attr}>{_escape(label)}</th>"
    is_active = column == active_sort
    next_dir = "desc" if is_active and active_dir == "asc" else "asc"
    params = {k: v[0] for k, v in query.items() if v and v[0] != "" and k not in ("sort", "dir")}
    params["sort"] = column
    params["dir"] = next_dir
    href = "?" + urlencode(params)
    aria = f' aria-sort="{"ascending" if active_dir == "asc" else "descending"}"' if is_active else ""
    classes = "sort is-active dir-" + active_dir if is_active else "sort"
    return f'<th{aria}{title_attr}><a class="{classes}" href="{_escape(href)}">{_escape(label)}</a></th>'


def _candidate_row(candidate, run_id, experiment_name, is_representative, running, output_summary):
    cid = candidate["candidate_id"]
    short = _short_id(cid)
    disabled = " disabled" if running else ""
    state_options = "".join(
        f'<option value="{option}"{" selected" if candidate["state"] == option else ""}>{option}</option>'
        for option in CANDIDATE_STATE_OPTIONS
    )
    checkbox = ""
    if candidate.get("screenable"):
        # form="generate-form" lets the checkbox live in the table body while
        # submitting into the <form> rendered above the table (§2/WB-UI-008).
        checkbox_disabled = " disabled" if running else ""
        checkbox = (
            f'<input type="checkbox" name="candidate" value="{_escape(cid)}" form="generate-form" '
            f'aria-label="候補 {_escape(short)} を選択"{checkbox_disabled}>'
        )
    quality = candidate.get("quality")
    quality_text = f"{quality:.4f}" if isinstance(quality, (int, float)) else "—"

    # WB-UI-014: the identity/generation-provenance columns (世代/個体/seed/
    # 役割/原記録/稿) and the grid/raw-log/output links move into a collapsed
    # detail row -- Sifting judges by cell/quality/reached/screenable/state
    # first, and only opens these to confirm identity or generation options.
    detail_id = f"detail-{_escape(cid)}"
    links = []
    if is_representative:
        cell = candidate.get("cell_key")
        links.append(
            f'<a href="/exp/{_url(experiment_name)}/cell/{_url(cell)}">格子で見る</a>'
        )
    if candidate["log"]["availability"] == "present":
        links.append(f'<a href="/runs/{_url(run_id)}/candidates/{_url(cid)}/raw">原ログ</a>')
    links.append(f'<a href="/outputs?run={_url(run_id)}">作品</a>')

    counts = (output_summary or {}).get(cid, {})
    syn_ok, nar_ok = counts.get("synopsize", 0), counts.get("narrate", 0)
    draft_text = f"あらすじ ok {syn_ok} / 上映 ok {nar_ok}" if (syn_ok or nar_ok) else "—"

    row = (
        f'<tr data-candidate-id="{_escape(cid)}">'
        f'<td>{checkbox}</td>'
        f'<td title="{_escape(cid)}">{_escape(short)}</td>'
        f'<td>{_escape(candidate.get("cell_key"))}</td>'
        f'<td>{_escape(quality_text)}</td>'
        f'<td>{_escape(_reached_text(candidate.get("reached")))}</td>'
        f'<td>{_escape(candidate.get("screenable"))}</td>'
        f'<td><select data-field="state" aria-label="選定状態 {_escape(short)}"{disabled}>'
        f'{state_options}</select></td>'
        f'<td><input data-field="note" aria-label="メモ {_escape(short)}" '
        f'value="{_escape(candidate.get("note", ""))}"{disabled}></td>'
        '<td class="wb-actions">'
        f'<button type="button" data-action="save-candidate"{disabled}>保存</button> '
        f'<button type="button" class="row-toggle" aria-expanded="false" aria-controls="{detail_id}">詳細</button>'
        '<span data-save-status></span>'
        "</td></tr>"
    )
    detail = (
        f'<tr id="{detail_id}" class="detail-row" hidden><td colspan="9">'
        '<dl class="metric">'
        f'<dt>{pages.term("candidate_generation", "世代")}</dt><dd>{_escape(candidate.get("generation"))}</dd>'
        f'<dt>{pages.term("individual", "個体")}</dt><dd>{_escape(candidate.get("individual_index"))}</dd>'
        f'<dt>{pages.term("candidate_seed", "seed")}</dt><dd>{_escape(candidate.get("seed"))}</dd>'
        f'<dt>{pages.term("role", "役割")}</dt><dd>{_escape(candidate.get("role"))}</dd>'
        f'<dt>{pages.term("log", "原記録")}</dt><dd>'
        f'{_escape(AVAILABILITY_LABELS.get(candidate["log"]["availability"], candidate["log"]["availability"]))}</dd>'
        f'<dt>{pages.term("draft", "稿")}</dt><dd>{_escape(draft_text)}</dd>'
        "</dl>"
        f'<p class="detail-links">{" ".join(links)}</p>'
        "</td></tr>"
    )
    return row + detail


def _candidates_filter_form(run_id, query):
    def val(name):
        values = query.get(name)
        return values[0] if values else ""

    def options(name, choices, empty_label=""):
        parts = [f'<option value=""{" selected" if val(name) == "" else ""}>{_escape(empty_label)}</option>']
        for choice, label in choices:
            parts.append(
                f'<option value="{_escape(choice)}"{" selected" if val(name) == choice else ""}>'
                f"{_escape(label)}</option>"
            )
        return "".join(parts)

    role_options = options("role", [(r, r) for r in ("protagonist", "antagonist", "unknown")])
    reached_options = options("reached", [("true", "到達"), ("false", "未到達")])
    availability_options = options("availability", list(AVAILABILITY_LABELS.items()))
    state_options = options("state", [(s, s) for s in CANDIDATE_STATE_OPTIONS])
    hidden_sort = ""
    if val("sort"):
        hidden_sort += f'<input type="hidden" name="sort" value="{_escape(val("sort"))}">'
    if val("dir"):
        hidden_sort += f'<input type="hidden" name="dir" value="{_escape(val("dir"))}">'
    return (
        '<form method="get" class="form-grid">'
        f"{hidden_sort}"
        '<div class="field"><label>世代'
        f'<input type="number" name="generation" value="{_escape(val("generation"))}"></label></div>'
        '<div class="field"><label>個体'
        f'<input type="number" name="individual_index" value="{_escape(val("individual_index"))}"></label></div>'
        '<div class="field"><label>seed'
        f'<input type="number" name="seed" value="{_escape(val("seed"))}"></label></div>'
        f'<div class="field"><label>役割<select name="role">{role_options}</select></label></div>'
        f'<div class="field"><label>到達<select name="reached">{reached_options}</select></label></div>'
        f'<div class="field"><label>原記録<select name="availability">{availability_options}</select></label></div>'
        f'<div class="field"><label>選定状態<select name="state">{state_options}</select></label></div>'
        '<div class="actions"><button type="submit">絞り込む</button>'
        f'<a href="/runs/{_url(run_id)}/candidates">解除</a></div>'
        "</form>"
    )


def _generate_form(run_id, has_adopted, running):
    narrate_disabled = running or not has_adopted
    narrate_note = "" if has_adopted else '<span class="muted">採用済みの候補がありません</span>'
    return (
        f'<form id="generate-form" method="get" action="/runs/{_url(run_id)}/generate">'
        f'<button type="submit" name="kind" value="synopsize"{" disabled" if running else ""}>'
        "あらすじを生成（選択した候補）</button> "
        f'<button type="submit" name="kind" value="narrate"{" disabled" if narrate_disabled else ""}>'
        "上映を生成（採用済み候補）</button> "
        f"{narrate_note}"
        "</form>"
    )


CANDIDATES_GLOSSARY_KEYS = (
    "run", "publication_revision", "selection_revision", "candidate_id",
    "candidate_generation", "individual", "candidate_seed", "role", "cell", "reached", "quality",
    "log", "screenable", "state", "note", "draft",
)


def render_candidates_page(*, run_id, experiment_name, config_id, revision, selection_revision,
                            candidates, representatives, running, query, representatives_error=False,
                            output_summary=None, output_summary_error=False, has_adopted=None,
                            sort_key=None, sort_dir="asc", running_job_id=None):
    header = (
        f'<p>{pages.term("run", "実験")}: {_escape(experiment_name)} · '
        f'{pages.term("publication_revision", "公開版")} {_escape(revision)} · '
        f'{pages.term("selection_revision", "選定版")} {_escape(selection_revision)} · '
        f'{len(candidates)}件</p>'
        '<p class="related">関連: '
        f'<a href="/exp/{_url(experiment_name)}">格子へ</a>'
        + (f'<a href="/configs/{_url(config_id)}">実行設定</a>' if config_id else "")
        + '<a href="/selected">横断 Sifting トレイ</a>'
        + '<a href="/outputs?run=' + _url(run_id) + '">作品一覧</a>'
        "</p>"
    )
    if running:
        job_link = f' <a href="/jobs/{_url(running_job_id)}">進捗を見る →</a>' if running_job_id else ""
        header += f'<p class="warning">実行中のため選定は保存できません{job_link}</p>'
    if representatives_error:
        header += '<p class="warning">代表セルを解決できないため［格子で見る］は表示しません</p>'
    if output_summary_error:
        header += '<p class="warning">稿の記録を読み取れないため件数を表示できません</p>'
    # has_adopted must reflect the *unfiltered* selection (the candidates
    # list here may be narrowed by the filter form), otherwise a filter that
    # hides every adopted row would wrongly grey out the narrate button.
    if has_adopted is None:
        has_adopted = any(c["state"] == "adopted" for c in candidates)
    generate_form = _generate_form(run_id, has_adopted, running)
    if candidates:
        rows = "".join(
            _candidate_row(c, run_id, experiment_name, c["candidate_id"] in representatives, running, output_summary)
            for c in candidates
        )
        def th(key, term_key=None):
            return _sort_th(_SORT_LABELS[key], key, query=query, active_sort=sort_key, active_dir=sort_dir,
                             term_key=term_key)

        headers = (
            "<th>選択</th>"
            f'<th title="{_th_title("candidate_id")}">候補ID</th>'
            + th("cell_key", "cell") + th("quality", "quality") + th("reached", "reached")
            + f'<th title="{_th_title("screenable")}">採用可</th>'
            + th("state", "state")
            + f'<th title="{_th_title("note")}">メモ</th>'
            + "<th>操作</th>"
        )
        table = (
            '<div class="grid-wrap"><table id="candidate-table" class="wb-table"><thead><tr>'
            f"{headers}"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    else:
        # No duplicate CTA here: the page-level next_action (set by the
        # caller from the run's *unfiltered* candidate count) already covers
        # "候補がありません" -> "実行する" (WB-UI-012 §2.3's "1 つだけ").
        table = "<p>候補がありません。</p>"
    return (
        f'<section id="candidates" data-wb="candidates" data-run-id="{_escape(run_id)}" '
        f'data-revision="{_escape(selection_revision)}">'
        + header + generate_form + _candidates_filter_form(run_id, query) + table
        + pages.glossary(CANDIDATES_GLOSSARY_KEYS)
        + "</section>"
    )


# --------------------------------------------------------------------------
# Cross-run tray
# --------------------------------------------------------------------------

def _tray_row(entry):
    cid = entry["candidate_id"]
    return (
        f'<tr data-run-id="{_escape(entry["run_id"])}" data-candidate-id="{_escape(cid)}" '
        f'data-revision="{_escape(entry["selection_revision"])}">'
        f'<td title="{_escape(cid)}">{_escape(_short_id(cid))}</td>'
        f'<td>{_escape(entry.get("cell_key"))}</td>'
        f'<td>{_escape(entry.get("generation"))}/{_escape(entry.get("seed"))}</td>'
        f'<td>{_escape(entry.get("state"))}</td>'
        f'<td>{_escape(entry.get("note"))}</td>'
        '<td class="wb-actions">'
        f'<a href="/runs/{_url(entry["run_id"])}/candidates">候補一覧</a>'
        f'<a href="/exp/{_url(entry["experiment_name"])}">格子</a>'
        '<button type="button" data-action="remove">外す</button>'
        "</td></tr>"
    )


def render_tray_page(rows):
    note = '<p class="muted">上映生成の操作は後続カード（WB-UI-008）</p>'
    if not rows:
        return "<p>採用・保留の候補はありません。</p>" + note
    by_run = {}
    for row in rows:
        by_run.setdefault(row["run_id"], []).append(row)
    sections = []
    for entries in by_run.values():
        experiment_name = entries[0]["experiment_name"]
        adopted_count = sum(e["state"] == "adopted" for e in entries)
        body = "".join(_tray_row(e) for e in entries)
        sections.append(
            f'<section class="card"><h2>{_escape(experiment_name)}（採用 {adopted_count} 件）</h2>'
            '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
            f'<th title="{_th_title("candidate_id")}">候補ID</th>'
            f'<th title="{_th_title("cell")}">セル</th>'
            "<th>世代/seed</th>"
            f'<th title="{_th_title("state")}">状態</th>'
            f'<th title="{_th_title("note")}">メモ</th>'
            "<th>操作</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div></section>"
        )
    return "".join(sections) + note


# --------------------------------------------------------------------------
# Route handlers
# --------------------------------------------------------------------------

def _config_world(config):
    return {"id": config["project_id"], "name": config["preview"]["world_name"]}


def _named_world(job_store, world_id):
    """{id, name} for a world with no pinned config of its own (still needs
    a header label -- see the /jobs?world= scoping in _jobs_list)."""
    if not world_id:
        return None
    match = next((w for w in pages._library_worlds(job_store) if w["id"] == world_id), None)
    return {"id": world_id, "name": (match["name"] if match else None) or world_id}


def _configs_list(handler):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    from execution.library import LibraryStore
    from viewer import library_pages  # deferred: library_pages imports this module
    configs = job_store.configs.list()
    body = (
        render_configs_list(configs)
        + library_pages.render_genres_section(LibraryStore(job_store.configs.repo).genres())
        + pages.glossary(("config_id", "genre"))
    )
    handler._send_html(pages.document(
        "設定", body,
        crumbs=[("設定", "/configs")], phase="world",
        lead="実行設定の版を作り、そこから GA を実行します。ジャンルの追加・編集もここで行います。",
        next_action=("新しい実行設定を作る →", "/configs/new"),
        job_store=job_store, pin=data.pinned_target(job_store, configs=configs),
    ))


def _configs_new(handler):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    query = _query(handler)
    from_id = query.get("from", [None])[0]
    repo = job_store.configs.repo
    projects = sorted(p.name for p in (repo / "projects").iterdir() if p.is_dir()) if (repo / "projects").is_dir() else []
    templates = sorted(p.name for p in (repo / "templates").iterdir() if p.is_dir()) if (repo / "templates").is_dir() else []
    if from_id is not None:
        parent = job_store.configs.get(from_id)
        values = _initial_values(
            label=parent["label"], project_id=parent["project_id"], template_id=parent["template_id"],
            evolution=parent["evolution"], execution_limits=parent["execution_limits"],
            generation=parent["generation"],
        )
        body = render_config_form(values, projects=projects, templates=templates, backends=BACKENDS,
                                   parent_config_id=from_id)
        title = "実行設定を複製"
    else:
        values = _new_config_values()
        project_preset = query.get("project", [None])[0]
        template_preset = query.get("template", [None])[0]
        if project_preset in projects:
            values["project_id"] = project_preset
        if template_preset in templates:
            values["template_id"] = template_preset
        elif project_preset in projects:
            # No explicit ?template=: default to the preset world's own genre
            # (execution/library.py's world.yaml gapengine.* resolution).
            from execution.library import LibraryStore
            world = next((w for w in LibraryStore(repo).worlds() if w["id"] == project_preset), None)
            if world and world["genre"] in templates:
                values["template_id"] = world["genre"]
        body = render_config_form(values, projects=projects, templates=templates, backends=BACKENDS)
        title = "新しい実行設定"
    handler._send_html(pages.document(
        title, body, crumbs=[("実行設定", "/configs"), (title, "/configs/new")], phase="world",
        lead="新しい実行設定を作り、GAの実行に使います。",
        job_store=job_store, pin=data.pinned_target(job_store),
    ))


def _configs_detail(handler, cid):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    config = job_store.configs.get(cid)
    label = config["label"]
    handler._send_html(pages.document(
        f"実行設定: {label}", render_config_detail(config),
        crumbs=[("実行設定", "/configs"), (label, f"/configs/{_url(cid)}")],
        phase="world", world=_config_world(config),
        lead="この設定版の内容を確認して実行します。",
        next_action=("この設定でGAを実行 →", f"/configs/{_url(cid)}/start"),
        job_store=job_store, pin=data.pinned_target(job_store),
    ))


def _configs_start(handler, cid):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    config = job_store.configs.get(cid)
    request_id = "req-" + uuid.uuid4().hex
    label = config["label"]
    handler._send_html(pages.document(
        f"{label} を実行", render_start_confirm(config, request_id),
        crumbs=[("実行設定", "/configs"), (label, f"/configs/{_url(cid)}"), ("実行確認", f"/configs/{_url(cid)}/start")],
        phase="world", world=_config_world(config),
        lead="実行内容を確認し、開始します。",
        job_store=job_store, pin=data.pinned_target(job_store),
    ))


def _duplicate(handler, cid):
    job_store = _job_store(handler)
    if job_store is None:
        raise ConfigError("service", "実行管理は未設定です", code="unavailable")
    handler.connection.settimeout(5)
    try:
        body = handler._request_json()
    except ValueError as error:
        raise ConfigError("request", "JSON本文が不正です", code="bad_request") from error
    job_api.boundary(handler)
    if set(body) != {"changes"} or not isinstance(body["changes"], dict):
        raise ConfigError("request", "変更内容をオブジェクトで指定してください", code="bad_request")
    settings_path = getattr(handler.server, "settings_path", None)
    document = job_store.configs.duplicate(cid, body["changes"], settings_path=settings_path)
    handler._send_json(HTTPStatus.CREATED, document)


def _jobs_next_action(records):
    """WB-UI-012 §2.2's /jobs row: running job's screen, else the most
    recently reachable Sifting target, else start a run. history() has no
    recency field (sorted by run_id), so "most recent" is approximated as
    the first candidate in its existing order -- no new ordering logic.
    """
    running = next((r for r in records if r["state"] in RUNNING_STATES and r.get("job_id")), None)
    if running:
        return "進捗を見る →", f"/jobs/{_url(running['job_id'])}"
    ready = next((r for r in records if r["state"] in ("succeeded", "partial")), None)
    if ready:
        return "Sifting へ →", f"/exp/{_url(ready['experiment_name'])}"
    return "実行設定を作る →", "/configs/new"


def _jobs_list(handler):
    repository = handler.repository
    if repository.catalog is None:
        handler._send_html(_guidance_page(phase="run"))
        return
    records = repository.catalog.history()
    job_store = _job_store(handler)
    generation_jobs = []
    if job_store is not None:
        try:
            generation_jobs = [j for j in job_store.list() if j.get("kind") in ("synopsize", "narrate")]
        except (ConfigError, OSError, ValueError, KeyError, TypeError):
            generation_jobs = []
    from viewer import output_pages
    # No duplicate empty-state CTA here: _jobs_next_action() already covers
    # "records is empty" -> "実行設定を作る" via the page-level next_action
    # below (WB-UI-012 §2.3's "1 つだけ").
    # ?world= arrives from a per-world page's "2 実行" tab (_phase_href) so
    # this scopes to *that* world instead of whichever world's config is
    # newest system-wide -- see data.pinned_target's world_id docstring.
    world_id = _query(handler).get("world", [None])[0]
    pin = data.pinned_target(job_store, world_id=world_id)
    world = pin["world"] if pin else _named_world(job_store, world_id)
    body = render_current_target_section(pin)
    body += render_jobs_list(records) + output_pages.render_generation_jobs_section(generation_jobs)
    body += pages.glossary(("job_state", "phase", "publication_revision", "config_id", "run"))
    handler._send_html(pages.document(
        "実行履歴", body, crumbs=[("実行履歴", "/jobs")], phase="run",
        world=world,
        job_store=job_store, pin=pin,
        lead="実行中と過去のジョブを見ます。",
        next_action=_jobs_next_action(records),
    ))


def _jobs_detail(handler, jid):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="run"))
        return
    job = job_store.get(jid)
    if job.get("kind") in ("synopsize", "narrate"):
        from viewer import output_pages
        body = output_pages.render_generation_job(job)
    else:
        body = render_job_page(job)
    # job["run_id"] is the catalog run_id (an evolve job's happens to equal
    # the experiment folder name since it always creates a native, non-legacy
    # run, but a synopsize/narrate job's does not for a legacy run). Resolve
    # it to the folder name for the header/Sifting link; keep the catalog id
    # for the 上映 link, which /outputs?run= matches against.
    catalog_run_id = job.get("run_id")
    # Unresolved stays None: a legacy catalog id in the 実験 picker would make
    # the Sifting link /exp/legacy-... and 404.
    run_name = None
    if catalog_run_id is not None:
        try:
            match = next(
                (r for r in handler.repository.catalog.history() if r["run_id"] == catalog_run_id),
                None,
            )
        except (ConfigError, OSError, ValueError, KeyError, TypeError, AttributeError):
            match = None
        if match is not None:
            run_name = match["experiment_name"]
    state = job.get("state")
    next_action = None
    if state in ("succeeded", "partial") and run_name:
        next_action = ("Sifting へ →", f"/exp/{_url(run_name)}")
    elif state in ("failed", "cancelled", "interrupted") and job.get("config_id"):
        next_action = ("実行設定へ →", f"/configs/{_url(job['config_id'])}")
    handler._send_html(pages.document(
        f"処理: {jid}", body, crumbs=[("実行履歴", "/jobs"), (jid, f"/jobs/{_url(jid)}")],
        phase="run", run=run_name, output_run=catalog_run_id,
        lead="実行の進み具合を見ます。完了したら候補を Sifting します。",
        next_action=next_action,
        job_store=job_store,
    ))


def _candidates_list(handler, run_id):
    repository = handler.repository
    if repository.catalog is None:
        handler._send_html(_guidance_page(phase="sifting"))
        return
    catalog, selections = repository.catalog, repository.selections
    query = _query(handler)
    sort_key = query.get("sort", [None])[0]
    if sort_key not in SORT_KEYS:
        sort_key = None
    sort_dir = query.get("dir", ["asc"])[0]
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"
    filter_query = {k: v for k, v in query.items() if k not in ("sort", "dir")}
    filters, state_filter = _parse_filters(_clean_query(filter_query))
    result = catalog.candidates(run_id, **filters)
    selected = selections.get(run_id)
    entries = {e["candidate_id"]: e for e in selected["entries"]}
    candidates = [
        {**c, "state": entries.get(c["candidate_id"], {}).get("state", "unclassified"),
         "note": entries.get(c["candidate_id"], {}).get("note", "")}
        for c in result["candidates"]
    ]
    if state_filter is not None:
        candidates = [c for c in candidates if c["state"] == state_filter]
    snapshot = catalog.snapshot(run_id)
    try:
        representatives = set(catalog.representatives(snapshot).values())
        representatives_error = False
    except ConfigError:
        # A broken/ambiguous representative mapping must not take the whole
        # Sifting page down; degrade to "no grid links" instead.
        representatives = set()
        representatives_error = True
    experiment_name = snapshot["experiment_name"]
    history_record = next((r for r in catalog.history() if r["run_id"] == run_id), None)
    running = history_record is not None and history_record["state"] in RUNNING_STATES
    running_job_id = history_record.get("job_id") if running and history_record else None
    config_id = history_record.get("config_id") if history_record else None
    # Unfiltered: the narrate button's enabled state must not depend on
    # whatever the filter form narrowed `candidates` down to above.
    has_adopted = any(e["state"] == "adopted" for e in selected["entries"])
    from viewer import output_pages
    summary = output_pages.run_output_summary(_job_store(handler), run_id)
    has_draft = any(sum(counts.values()) > 0 for counts in summary["by_candidate"].values())
    if sort_key:
        candidates = sort_candidates(candidates, sort_key, sort_dir, summary["by_candidate"])
    page = render_candidates_page(
        run_id=run_id, experiment_name=experiment_name, config_id=config_id,
        revision=result["revision"], selection_revision=selected["revision"],
        candidates=candidates, representatives=representatives, running=running, query=query,
        representatives_error=representatives_error, output_summary=summary["by_candidate"],
        output_summary_error=summary["error"], has_adopted=has_adopted,
        sort_key=sort_key, sort_dir=sort_dir, running_job_id=running_job_id,
    )
    # WB-UI-012 §2.2/§2.3: a run with no candidates at all sends the user
    # back to start a run; otherwise the three-way adopt/generate/read
    # judgement (snapshot["candidates"] is the *unfiltered* total, already
    # fetched above -- distinct from `candidates`, which the filter form may
    # have narrowed).
    if not snapshot["candidates"]["candidates"]:
        next_action = ("実行する →", "/configs/new")
    elif not has_adopted:
        next_action = ("候補を採用する（選定状態を adopted に）→", "#candidate-table")
    elif not has_draft:
        next_action = ("あらすじを生成する →", "#generate-form")
    else:
        next_action = ("作品を読む →", f"/outputs?run={_url(run_id)}")
    handler._send_html(pages.document(
        f"候補: {experiment_name}", page,
        crumbs=[(experiment_name, f"/exp/{_url(experiment_name)}"), ("候補一覧", f"/runs/{_url(run_id)}/candidates")],
        # run_id here is the catalog run_id (from the URL); it's what
        # /outputs?run= must use, while `run` (the experiment folder name)
        # drives the header's picker and its /exp/ link.
        phase="sifting", run=experiment_name, output_run=run_id,
        lead="候補に選定状態を付け、採用した候補から本文を生成します。",
        next_action=next_action,
        job_store=_job_store(handler),
    ))


def _candidates_raw(handler, run_id, candidate_id):
    repository = handler.repository
    if repository.catalog is None:
        handler._send_html(_guidance_page(phase="sifting"))
        return
    catalog = repository.catalog
    experiment_name = catalog.snapshot(run_id)["experiment_name"]
    result = catalog.candidates(run_id)
    candidate = next((c for c in result["candidates"] if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise ConfigError("candidate_id", "候補がありません", code="not_found")
    availability = candidate["log"]["availability"]
    relative_path = candidate["log"].get("relative_path")
    if availability != "present" or not relative_path:
        raise ConfigError("candidate_id", f"原記録を表示できません（状態: {availability}）", code="not_found")
    root, _legacy = catalog.resolve(run_id)
    path = contained(root, relative_path)
    raw = path.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    query = _query(handler)
    line_param = query.get("line", [None])[0]
    body = [f'<p><a href="/runs/{_url(run_id)}/candidates">← 候補一覧へ戻る</a></p>']
    body.append(
        f'<p>SHA-256: {_escape(candidate.get("source_log_sha256"))} · '
        f'世代{_escape(candidate.get("generation"))} · '
        f'個体{_escape(candidate.get("individual_index"))} · seed{_escape(candidate.get("seed"))}</p>'
    )
    if line_param is not None:
        try:
            line = int(line_param)
        except (TypeError, ValueError) as error:
            raise ConfigError("line", "1始まりの整数を指定してください") from error
        if not 1 <= line <= len(lines):
            raise ConfigError("line", "原ログの範囲外です")
        first, last = max(1, line - 3), min(len(lines), line + 3)
        body.append(
            f'<p>原ログ全{len(lines)}行のうちL{first}〜L{last}。'
            f'<a href="/runs/{_url(run_id)}/candidates/{_url(candidate_id)}/raw">全文</a></p>'
        )
    else:
        first, last = 1, len(lines)
    body.append('<div class="raw-lines">')
    for number in range(first, last + 1):
        body.append(f'<pre id="L{number}"><a href="?line={number}#L{number}">L{number}</a> {_escape(lines[number - 1])}</pre>')
    body.append("</div>")
    handler._send_html(pages.document(
        f"{candidate_id} 原ログ", "".join(body),
        crumbs=[("候補一覧", f"/runs/{_url(run_id)}/candidates")],
        phase="sifting", run=experiment_name, output_run=run_id,
        job_store=_job_store(handler),
    ))


def _tray(handler):
    repository = handler.repository
    if repository.selections is None:
        handler._send_html(_guidance_page(phase="sifting"))
        return
    rows = repository.selections.tray()
    body = f'<section data-wb="tray">{render_tray_page(rows)}</section>'
    handler._send_html(pages.document(
        "Sifting トレイ", body, crumbs=[("Sifting トレイ", "/selected")], phase="sifting",
        lead="実験をまたいで採用した候補をまとめて見ます。",
        next_action=("作品一覧へ →", "/outputs"),
        job_store=_job_store(handler),
    ))


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _resolve(parts, method):
    if method == "POST":
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "configs" and parts[3] == "duplicate":
            return _duplicate, (parts[2],)
        return None
    if method != "GET" or not parts:
        return None
    head = parts[0]
    if head == "configs":
        if len(parts) == 1:
            return _configs_list, ()
        if len(parts) == 2 and parts[1] == "new":
            return _configs_new, ()
        if len(parts) == 2:
            return _configs_detail, (parts[1],)
        if len(parts) == 3 and parts[2] == "start":
            return _configs_start, (parts[1],)
        return None
    if head == "jobs":
        if len(parts) == 1:
            return _jobs_list, ()
        if len(parts) == 2:
            return _jobs_detail, (parts[1],)
        return None
    if head == "runs":
        if len(parts) == 3 and parts[2] == "candidates":
            return _candidates_list, (parts[1],)
        if len(parts) == 5 and parts[2] == "candidates" and parts[4] == "raw":
            return _candidates_raw, (parts[1], parts[3])
        return None
    if head == "selected" and len(parts) == 1:
        return _tray, ()
    return None


def dispatch(handler, parts, method):
    route = _resolve(parts, method)
    if route is None:
        return False
    action, args = route
    try:
        action(handler, *args)
    except ConfigError as error:
        job_api.send_error(handler, error)
    except FileNotFoundError:
        job_api.send_error(handler, ConfigError("resource", "公開済み記録がありません", code="not_found"))
    except (OSError, ValueError, TypeError, KeyError):
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
            "code": "storage_error", "message": "保存済み記録を処理できません",
            "field_errors": {}, "retryable": False, "current_revision": None,
        })
    return True
