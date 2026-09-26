"""HTML workbench: configuration forms, job monitoring, and Sifting (WB-UI-007).

Every state change goes through the existing JSON APIs (job_api.py,
run_catalog.py); this module only renders HTML and adds the one missing
HTTP route (duplicate-save). GA execution never touches an LLM.
"""
from __future__ import annotations

from http import HTTPStatus
import json
import math
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

from execution.configs import evolution_defaults, generation_availability, quick_label
from execution.output_settings import API_KEY_BACKENDS, read_output_settings
from execution.provenance import ConfigError, contained
from execution.worker import TERMINAL
from gapengine.evolve import _load_yaml, _rationality_backend_cfg
from gapengine.ollama import DEFAULT_BASE_URL as RATIONALITY_DEFAULT_BASE_URL
from gapengine.ollama import DEFAULT_MODEL as RATIONALITY_DEFAULT_MODEL
from gapengine.ollama import availability as _ollama_availability
from viewer import data, explanation_ui, job_api, pages, world_graph


_escape = pages._escape
_url = pages._url_segment

RUNNING_STATES = data.RUNNING_JOB_STATES

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
    "proposing": "拡張を提案中",
    "checking": "拡張を検査中",
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
# Japanese labels for the selection state as shown in the Sifting UI (the
# <select> in the candidate table, the filter form and the tray badge). The
# leading mark makes the four states scannable at a glance: ✔ adopted (green),
# ⏸ held (amber), ✖ rejected (red), ○ unclassified (grey) -- colours live in
# app.css under .state-sel-*.
CANDIDATE_STATE_LABELS = {
    "adopted": "✔ 採用",
    "held": "⏸ 保留",
    "rejected": "✖ 除外",
    "unclassified": "○ 未分類",
}
AVAILABILITY_LABELS = {"present": "あり", "pruned": "剪定済み", "missing": "不在", "stale": "不一致"}
# WB-WORLDGROW-001 stage 3a: evolution.world_expansion's Japanese display value
# (config form's radio + detail views). An unrecognized value is shown as-is.
WORLD_EXPANSION_LABELS = {"off": "しない", "detect": "検知のみ", "expand": "承認済みの拡張を適用"}


def _world_expansion_label(value):
    return WORLD_EXPANSION_LABELS.get(value, value)


def _seed_genomes_label(value):
    """WB-WORLDGROW-001 段階5b: config detail views' "前の実験から引き継ぐ" row."""
    return value if value else "しない"


GROWTH_MODE_LABELS = {"auto": "自動", "manual": "手動（承認ごとに止まる）"}


def _growth_label(growth):
    """WB-WORLDGROW-001 段階5c-2: config detail views' "世界を育てる" row.
    growth is config.get("growth") -- None (or missing) means off, same
    contract as execution/configs.py's own "no key at all" convention."""
    if not growth or growth.get("mode", "off") == "off":
        return "しない"
    mode_label = GROWTH_MODE_LABELS.get(growth["mode"], growth["mode"])
    return f'{mode_label} {growth.get("epochs")} 周'

# WB-UI-021: /configs's 文章生成 card (execution/output_settings.py's backend choices).
GENERATION_BACKEND_OPTIONS = (
    ("claude-cli", "Claude Code CLI（claude -p）"),
    ("codex-cli", "Codex CLI（codex exec）。既定"),
    ("anthropic", "Anthropic API（api_key が必要）"),
    ("openai", "OpenAI API（api_key が必要）"),
    ("ollama", "ローカル Ollama"),
    ("llama-server", "ローカル llama-server（OpenAI互換。Bonsai 2 など）"),
    ("none", "生成しない（プロンプト保存のみ）"),
)
GENERATION_REASON_LABELS = {
    "executable_missing": "実行ファイルが見つかりません",
    "credentials_missing": "資格情報がありません",
    "model_required": "モデルを指定してください",
    "settings_unreadable": "settings.json を読めません",
    "invalid_model": "モデル名が不正です",
    "model_missing": "そのモデルは見つかりません",
    "server_unreachable": "サーバーに接続できません",
}

# WB-JEV-002: the config form's κ slider section. Description/note text is
# used verbatim (plan §3).
RATIONALITY_DESCRIPTION = (
    "毎ターン、判定器が候補の行動それぞれに「本人の知る限りで目的に近づく手か」の確率を付けます。"
    "κ はその判定にどれだけ従うかの強さです。0 で無効（従来と同じ動き）、大きいほど筋の通った手を"
    "選びやすくなります。性格（遺伝子）の違いはこの範囲の中で効きます。"
)
RATIONALITY_NOTE = "κ を 0 より大きくすると判定器（ローカル LLM）を呼ぶため、実行に時間がかかります。"
# Opus review: reasons specific to the rationality judge probe (not
# generation_availability()'s vocabulary, which GENERATION_REASON_LABELS
# above covers) -- kept separate so the two reason namespaces never collide.
RATIONALITY_REASON_LABELS = {
    "invalid_base_url": "設定の base_url が不正です",
    "non_ollama_backend": "ollama 以外の判定器は画面から使えません",
}
# Stage 3 measured ~90s/run at kappa>0 (one Ollama /api/chat call per
# candidate action, roughly 4.2s x ~20 calls) -- used only for the config
# form's ETA hint below, never for anything that gates or blocks a save.
# ponytail: fixed constant estimate; could instead be derived from recent
# generation_summary judge-call timings if this proves too rough in practice.
JUDGE_SECONDS_PER_RUN = 90


def _format_eta_seconds(seconds):
    """"約90秒" / "約9分" / "約2時間30分" (no "0分" when the hour count is
    exact) -- shared by the config form's initial server render and
    workbench.js's live recompute (coordinator review), so the two can never
    disagree on wording."""
    if seconds < 60:
        return f"約{round(seconds)}秒"
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return f"約{minutes}分"
    hours, remaining_minutes = divmod(minutes, 60)
    if remaining_minutes:
        return f"約{hours}時間{remaining_minutes}分"
    return f"約{hours}時間"


def availability_label(availability):
    """Japanese text for a generation_availability()-shaped result.

    Shared by the /configs card's initial render, the settings API's
    `availability.label` (read by workbench.js), and JS itself -- so the
    reason wording lives in exactly one place (WB-UI-021 review item 3).
    """
    if availability.get("available"):
        return "利用可能"
    reason = availability.get("reason")
    return GENERATION_REASON_LABELS.get(reason, reason or "利用できません")


def reason_label(reason):
    """Japanese text for a bare reason code (list_models()'s "reason" field)."""
    if reason is None:
        return None
    return GENERATION_REASON_LABELS.get(reason, reason)


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

def selection_state_badge(state):
    """Badge for the Sifting *selection* state (採用/保留/除外/未分類), coloured
    via .state-sel-<state> so it reads the same as the row <select>."""
    label = CANDIDATE_STATE_LABELS.get(state, str(state))
    return f'<span class="state-badge state-sel-{_escape(state)}">{_escape(label)}</span>'


def state_badge(state, labels=STATE_LABELS):
    label = labels.get(state, str(state))
    # data-field="state" lives on this element itself (not a wrapping <p>) so
    # polling JS can swap both its className and its label span uniquely.
    return (
        f'<span class="state-badge state-{_escape(state)}" data-field="state">'
        f'<span data-field="state-label">{_escape(label)}</span></span>'
    )


def _dl(pairs, *, cls="metric"):
    items = "".join(f"<dt>{_escape(label)}</dt><dd>{value}</dd>" for label, value in pairs)
    return f'<dl class="{cls}">{items}</dl>'


def _short_id(value):
    text = str(value)
    return text[:12] + "…" if len(text) > 12 else text


def _th_title(term_key):
    return _escape(pages.TERM_HELP[term_key])


def _guidance_page(title="実行管理", *, phase=None):
    from viewer.error_pages import guidance
    return guidance("この操作はStudioで利用できます", "実行管理は未設定です。保存された物語は、ホームで世界を開き「保存された物語を見る」から読めます。", phase=phase)


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

def _field_key(name):
    """Internal name shown next to a field's Japanese label (mono, lower).

    Strips the leading "evolution." namespace prefix (kept for
    execution_limits.* and bare names like "label") so the key matches what a
    config JSON author would actually type under evolution:.
    """
    if name.startswith("evolution."):
        return name[len("evolution."):]
    return name


def _key_span(name):
    return f' <span class="key">{_escape(_field_key(name))}</span>'


def _hint_html(hint):
    return f'<p class="hint">{_escape(hint)}</p>' if hint else ""


def _text_field(label, name, value, *, required=False, placeholder="", hint="", list_id=None):
    req = " required" if required else ""
    ph = f' placeholder="{_escape(placeholder)}"' if placeholder else ""
    lst = f' list="{_escape(list_id)}"' if list_id else ""
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">{_escape(label)}{_key_span(name)}</label>'
        f'<input id="f-{_escape(name)}" type="text" name="{_escape(name)}" '
        f'data-field="{_escape(name)}" value="{_escape(value)}"{req}{ph}{lst}>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        f'{_hint_html(hint)}'
        "</div>"
    )


def _number_field(label, name, value, *, unit="", min_value=None, max_value=None, hint=""):
    minattr = f' min="{_escape(min_value)}"' if min_value is not None else ""
    maxattr = f' max="{_escape(max_value)}"' if max_value is not None else ""
    input_html = (
        f'<input id="f-{_escape(name)}" type="number" step="1" name="{_escape(name)}" '
        f'data-field="{_escape(name)}" value="{_escape(value)}"{minattr}{maxattr}>'
    )
    if unit:
        input_html = f'<div class="unit">{input_html}<span>{_escape(unit)}</span></div>'
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">{_escape(label)}{_key_span(name)}</label>'
        f'{input_html}'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        f'{_hint_html(hint)}'
        "</div>"
    )


def _select_field(label, name, options, selected, *, hint="", option_data=None):
    option_data = option_data or {}
    opts = []
    for option in options:
        extra = "".join(
            f' data-{_escape(key)}="{_escape(val)}"' for key, val in (option_data.get(option) or {}).items()
        )
        sel = " selected" if option == selected else ""
        opts.append(f'<option value="{_escape(option)}"{sel}{extra}>{_escape(option)}</option>')
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">{_escape(label)}{_key_span(name)}</label>'
        f'<select id="f-{_escape(name)}" name="{_escape(name)}" data-field="{_escape(name)}">{"".join(opts)}</select>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        f'{_hint_html(hint)}'
        "</div>"
    )


def _checkbox_field(label, name, checked, *, desc=""):
    chk = " checked" if checked else ""
    return (
        '<div class="field">'
        f'<label class="toggle" for="f-{_escape(name)}">'
        f'<input id="f-{_escape(name)}" type="checkbox" name="{_escape(name)}" '
        f'data-field="{_escape(name)}"{chk}><b>{_escape(label)}</b><small>{_escape(desc)}</small></label>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        "</div>"
    )


def _described_select_field(label, name, options, selected):
    """Like _select_field, but options are (value, description) pairs shown
    inline in each <option> -- for a compact dropdown standing in for what
    would otherwise be a _radio_field's row of cards (WB-UI-021 output card)."""
    opts = []
    for value, desc in options:
        sel = " selected" if value == selected else ""
        text = f"{value} — {desc}" if desc else value
        opts.append(f'<option value="{_escape(value)}"{sel}>{_escape(text)}</option>')
    return (
        '<div class="field">'
        f'<label for="f-{_escape(name)}">{_escape(label)}{_key_span(name)}</label>'
        f'<select id="f-{_escape(name)}" name="{_escape(name)}" data-field="{_escape(name)}">{"".join(opts)}</select>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        "</div>"
    )


def _radio_field(label, name, options, selected):
    choices = "".join(
        '<label class="choice">'
        f'<input type="radio" id="f-{_escape(name)}-{_escape(value)}" name="{_escape(name)}" '
        f'value="{_escape(value)}" data-field="{_escape(name)}"{" checked" if value == selected else ""}>'
        f'<b>{_escape(value)}</b><small>{_escape(desc)}</small></label>'
        for value, desc in options
    )
    return (
        '<div class="field">'
        f'<span class="field-label">{_escape(label)}{_key_span(name)}</span>'
        f'<div class="choices">{choices}</div>'
        f'<span class="field-error" data-error-for="{_escape(name)}" role="alert"></span>'
        "</div>"
    )


def _section(title, desc, body):
    return (
        f'<section class="cfg-sec"><div class="cfg-sec-head"><h2>{_escape(title)}</h2>'
        f'<p class="desc">{_escape(desc)}</p></div><div class="cfg-sec-body">{body}</div></section>'
    )


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _initial_values(*, label, project_id, template_id, evolution, execution_limits, growth=None):
    values = {"label": label, "project_id": project_id, "template_id": template_id}
    for key in ("generations", "population", "seeds", "seed_base", "ga_seed", "processes"):
        values[f"evolution.{key}"] = evolution[key]
    values["evolution.keep"] = evolution["keep"]
    values["evolution.world_expansion"] = evolution.get("world_expansion", "off")
    # WB-WORLDGROW-001 段階5b: .get() -- a config saved before this stage
    # added "seed_genomes" to evolution_defaults() has no such key either.
    values["evolution.seed_genomes"] = evolution.get("seed_genomes")
    for key in ("coevolve", "meta_evolution", "record_explanations"):
        values[f"evolution.{key}"] = evolution[key]
    # .get(), not [...]: a config saved before WB-JEV-002 added "kappa" to
    # evolution_defaults() has no such key at all.
    values["evolution.kappa"] = evolution.get("kappa")
    # .get(): a config saved before WB-ROUTE-001 S4 added "route_rho" to
    # evolution_defaults() has no such key either.
    values["evolution.route_rho"] = evolution.get("route_rho")
    values["evolution.target_ending"] = (
        ", ".join(evolution["target_ending"]) if evolution.get("target_ending") else ""
    )
    values["execution_limits.wall_seconds"] = execution_limits["wall_seconds"]
    # WB-WORLDGROW-001 段階5c-2: growth is None for a brand-new form and for
    # any config saved before this stage (execution/configs.py's own "no
    # key at all means off" convention -- see _growth()).
    growth = growth or {}
    values["growth.mode"] = growth.get("mode", "off")
    values["growth.epochs"] = growth.get("epochs", 3)
    values["growth.auto_retire"] = growth.get("auto_retire", values["growth.mode"] == "auto")
    return values


def _rationality_form_context(repo, template_id):
    """Whether templates/<template_id>/rationality.yaml exists, and the local
    judge's live reachability (WB-JEV-002). None when the template has no
    rationality.yaml at all -- render_config_form then leaves the κ slider
    out of the form entirely and kappa stays None. Only ever reads Ollama's
    /api/tags (gapengine.ollama.availability); never calls /api/chat or
    /api/generate."""
    if not template_id:
        return None
    path = repo / "templates" / template_id / "rationality.yaml"
    if not path.is_file():
        return None
    doc = _load_yaml(path, {}) or {}
    if not isinstance(doc, dict):
        return None
    backend = dict(doc.get("backend") or {})
    method = str(doc.get("method", "noul"))
    model = str(backend.get("model", RATIONALITY_DEFAULT_MODEL))
    if str(backend.get("type", "none")) != "ollama":
        return {"model": model, "method": method, "available": False,
                "reason": "non_ollama_backend"}
    base_url = str(backend.get("base_url", RATIONALITY_DEFAULT_BASE_URL))
    if not base_url.startswith(("http://", "https://")):
        # Opus review: rationality.yaml is editable from the genre/template
        # editor, so a malformed base_url must be caught here -- never
        # attempt a connection just from opening this form.
        return {"model": model, "method": method, "available": False,
                "reason": "invalid_base_url"}
    probe = _ollama_availability({"model": model, "base_url": base_url}, timeout=2.0)
    return {"model": model, "method": method,
            "available": bool(probe["available"]), "reason": probe["reason"]}


def _route_form_context(repo, template_id):
    """WB-ROUTE-001 S4 §2: whether templates/<template_id>/route.yaml
    exists. Unlike κ's context, ρ needs no live probe (there is no external
    judge to reach) -- None leaves the ρ section out of the form entirely,
    same None-means-no-section contract as _rationality_form_context."""
    if not template_id:
        return None
    path = repo / "templates" / template_id / "route.yaml"
    return {} if path.is_file() else None


def _frozen_template_dir(control, config):
    """The template snapshot frozen into this config at save time (WB-JEV-002
    Opus review), under this config's own control/configs/<id>/inputs --
    never the live repo, which may have been edited since. Every saved
    config has this directory (ConfigStore._capture_inputs freezes the whole
    template), whether or not rationality.yaml happened to be among its
    files."""
    return control / "configs" / config["config_id"] / "inputs" / "templates" / config["template_id"]


def _rationality_summary(template_dir, evolution):
    """"0.6（choice / qwen3.6:35b）" or "無効", for the saved-config detail
    page and the run screen's config summary (WB-JEV-002). template_dir must
    be the FROZEN per-config template snapshot (see _frozen_template_dir),
    not the live repo -- a template edited after this config was saved must
    never change what this text says a run would do. Reuses
    gapengine.evolve._rationality_backend_cfg's own override precedence so
    this text can never disagree with what a run would actually do."""
    kappa = evolution.get("kappa")
    if kappa is None:
        return "無効"
    rationality_yaml, rationality_yaml_backend, override = _rationality_backend_cfg(
        {"rationality": {"method": evolution.get("rationality_method")}}, template_dir,
    )
    method = override.get("method") or rationality_yaml.get("method", "noul")
    model = rationality_yaml_backend.get("model", RATIONALITY_DEFAULT_MODEL)
    return f"{kappa}（{method} / {model}）"


def _route_rho_summary(evolution):
    """"1.0" or "無効", for the saved-config detail page and the run
    screen's config summary (WB-ROUTE-001 S4 §2, mirrors
    _rationality_summary's own contract but needs no frozen template dir --
    ρ has no backend/model to report)."""
    rho = evolution.get("route_rho")
    return "無効" if rho is None else str(rho)


def _kappa_field(value):
    return (
        '<div class="field">'
        '<label for="f-evolution.kappa">κ（0〜1）<span class="key">kappa</span></label>'
        '<div class="kappa-row">'
        f'<input id="f-evolution.kappa" type="range" name="evolution.kappa" '
        f'data-field="evolution.kappa" min="0" max="1" step="0.05" value="{_escape(value)}" '
        'aria-describedby="kappa-desc kappa-status">'
        f'<output for="f-evolution.kappa" data-kappa-output>{_escape(value)}</output>'
        "</div>"
        '<span class="field-error" data-error-for="evolution.kappa" role="alert"></span>'
        "</div>"
    )


def _rationality_section(values, ctx, *, total_runs, wall_seconds, heading_prefix=""):
    kappa_value = values.get("evolution.kappa") or 0
    if ctx["available"]:
        status = f'判定器: Ollama {ctx["model"]} — 利用可'
    elif ctx["reason"] == "non_ollama_backend":
        # Not actually Ollama -- "判定器: Ollama <model>" would be misleading.
        status = f'判定器: 利用不可（{RATIONALITY_REASON_LABELS["non_ollama_backend"]}）。既定は 0 です'
    else:
        reason = {**GENERATION_REASON_LABELS, **RATIONALITY_REASON_LABELS}.get(
            ctx["reason"], ctx["reason"] or "不明")
        status = f'判定器: Ollama {ctx["model"]} — 利用不可（{reason}）。既定は 0 です'
    # WB-JEV-002 coordinator review: kappa/generations/population/seeds/
    # coevolve/wall_seconds can all change client-side without a reload, so
    # this eta/warning pair is always rendered (never omitted), toggled via
    # the "hidden" attribute -- workbench.js's updateRationality() flips the
    # same attribute and rewrites [data-kappa-eta-text] on every input event,
    # using this section's own data-judge-seconds-per-run so the 90s/run
    # constant is never hardcoded twice. JS-off: this initial render (computed
    # the same way, see _format_eta_seconds) is exactly what stays visible.
    show_eta = bool(kappa_value and total_runs)
    seconds = total_runs * JUDGE_SECONDS_PER_RUN if show_eta else 0
    show_warning = show_eta and wall_seconds is not None and seconds > wall_seconds
    eta_html = (
        f'<p class="hint" data-kappa-eta{"" if show_eta else " hidden"}>判定器の見込み: '
        f'<span data-kappa-eta-text>{_escape(_format_eta_seconds(seconds) if show_eta else "")}</span>'
        "（1 ラン約90秒で概算。表が育つほど短くなります）</p>"
        f'<p class="warning" data-kappa-warning{"" if show_warning else " hidden"}>'
        "判定器の見込みが実行時間の上限を超えています。"
        "上限を見直すか、規模を小さくしてください。</p>"
    )
    return (
        '<section class="cfg-sec" data-wb="rationality" '
        f'data-judge-seconds-per-run="{JUDGE_SECONDS_PER_RUN}"><div class="cfg-sec-head">'
        f"<h2>{_escape(heading_prefix)}合理性（主人公がどれだけ筋の通った手を選ぶか）</h2>"
        f'<p class="desc" id="kappa-desc">{_escape(RATIONALITY_DESCRIPTION)}</p>'
        '</div><div class="cfg-sec-body">'
        + _kappa_field(kappa_value)
        + '<p class="hint">0 無効 / 0.3 穏やか / 0.6 推奨 / 1.0 ほぼ判定器どおり</p>'
        + f'<p class="hint" id="kappa-status">{_escape(status)}</p>'
        + f'<p class="hint">{_escape(RATIONALITY_NOTE)}</p>'
        + eta_html
        + "</div></section>"
    )


def _route_rho_field(value):
    return (
        '<div class="field">'
        '<label for="f-evolution.route_rho">ρ（0〜1）<span class="key">route_rho</span></label>'
        '<div class="kappa-row">'
        f'<input id="f-evolution.route_rho" type="range" name="evolution.route_rho" '
        f'data-field="evolution.route_rho" min="0" max="1" step="0.05" value="{_escape(value)}" '
        'aria-describedby="route-rho-desc">'
        f'<output for="f-evolution.route_rho" data-route-rho-output>{_escape(value)}</output>'
        "</div>"
        '<span class="field-error" data-error-for="evolution.route_rho" role="alert"></span>'
        "</div>"
    )


def _route_section(values, *, heading_prefix=""):
    """WB-ROUTE-001 S4 §2: "05. 道筋" -- only rendered when the template has
    a route.yaml at all (caller checks _route_form_context first, same
    None-means-omit contract as _rationality_section)."""
    rho_value = values.get("evolution.route_rho") or 0
    return (
        '<section class="cfg-sec" data-wb="route"><div class="cfg-sec-head">'
        f"<h2>{_escape(heading_prefix)}道筋（寄り道の抑え方）</h2>"
        '<p class="desc" id="route-rho-desc">主人公が結末へまっすぐ向かう強さ。'
        "0 で従来どおり、1 で理由のない寄り道をほとんど選ばない。"
        "寄り道の理由は動機表（motives.yaml）で与える。</p>"
        '</div><div class="cfg-sec-body">'
        + _route_rho_field(rho_value)
        + "</div></section>"
    )


def _new_config_values():
    defaults = evolution_defaults()
    return _initial_values(
        label="", project_id="", template_id="",
        evolution=defaults,
        execution_limits={"wall_seconds": 3600},
    )


def render_config_form(values, *, projects, templates, parent_config_id=None, world_genres=None,
                        rationality=None):
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
        option_data = {world: {"genre": genre} for world, genre in (world_genres or {}).items() if genre}
        project_block = _select_field("世界", "project_id", projects, values["project_id"], option_data=option_data)
        template_block = _select_field(
            "ジャンル", "template_id", templates, values["template_id"],
            hint="世界を選ぶと、その世界の既定ジャンルに揃います。",
        )

    gens = _as_int(values["evolution.generations"])
    population = _as_int(values["evolution.population"])
    seeds = _as_int(values["evolution.seeds"])
    per_gen = population * seeds
    total = gens * population * seeds

    section1 = _section(
        "基本", "何の世界を、どの結末に向けて探索するか。",
        '<div class="rows">'
        + _text_field("設定名", "label", values["label"], required=True,
                       placeholder="例: 桃太郎・鬼退治ルート 20世代")
        + "</div>"
        + f'<div class="cols">{project_block}{template_block}</div>'
        + '<div class="rows">'
        + _text_field(
            "目指す結末", "evolution.target_ending", values["evolution.target_ending"],
            placeholder="空なら世界の既定", hint="複数あるときはカンマ区切り。",
        )
        + "</div>",
    )

    section2 = _section(
        "探索の規模",
        "1 世代 = 個体数 × seed 数のラン。世代を重ねるほど良い経緯が残りますが、時間も比例します。",
        '<div class="cols">'
        + _number_field("世代数", "evolution.generations", values["evolution.generations"], unit="世代", min_value=1)
        + _number_field("個体数", "evolution.population", values["evolution.population"], unit="個体", min_value=1)
        + _number_field(
            "seed数", "evolution.seeds", values["evolution.seeds"], unit="通り", min_value=1,
            hint="同じ遺伝子でも seed が違えば世界の初期条件が変わる。",
        )
        + "</div>"
        + f'<p class="calc" data-calc>1 世代あたり <b data-per-gen>{per_gen:,}</b> ラン、'
          f'全体で <b data-total>{total:,}</b> ラン。</p>'
        + _radio_field(
            "残すラン", "evolution.keep",
            (
                ("all", "全ランの層ログを残す。容量は最大。"),
                ("reached", "結末に届いたランだけ残す（1 本も届かなければ最良個体のランを残す）。既定。"),
                ("exemplar", "個体ごとに最良の到達ランを 1 本だけ残す。最小。"),
            ),
            values["evolution.keep"],
        ),
    )

    section3 = _section(
        "進化の設定", "探索の仕方を切り替えるスイッチ。",
        '<div class="toggles">'
        + _checkbox_field(
            "共進化", "evolution.coevolve", values["evolution.coevolve"],
            desc="敵役の個体群とアーカイブを別に持ち、主人公側と並行して進化させる。",
        )
        + _checkbox_field(
            "メタ進化", "evolution.meta_evolution", values["evolution.meta_evolution"],
            desc="9 つのスカラー遺伝子に加えて、ルールごとの有効／無効ビットも進化させる。",
        )
        + _checkbox_field(
            "説明記録", "evolution.record_explanations", values["evolution.record_explanations"],
            desc="各手番の「選択・根拠・代償・転機」を記録する。上映で使う。",
        )
        + "</div>"
        + _radio_field(
            "世界の拡張", "evolution.world_expansion",
            (
                ("off", "しない（既定）。世界は設定したまま固定。"),
                ("detect", "検知のみ。進化の後に、世界の解像度が足りない場所"
                           "（よく滞在するのに行動が空振りする場所）を集計して実験画面に出す。世界は変えない。"),
                ("expand", "承認済みの拡張を適用。この世界に承認済みの拡張パッチがあれば当てた世界で回し、需要の集計もする。"),
            ),
            values["evolution.world_expansion"],
        ),
    )

    section4 = (
        _rationality_section(
            values, rationality,
            # Opus review: match execution/configs.py's _describe()
            # planned_seed_evaluations, which is what a coevolve run actually
            # judges (protagonist pass + a separate antagonist pass).
            total_runs=total * (2 if values["evolution.coevolve"] else 1),
            wall_seconds=_as_int(values["execution_limits.wall_seconds"]),
        )
        if rationality is not None else ""
    )

    section5a = _section(
        "乱数と並列", "同じ値なら同じ結果になります（決定論）。",
        '<div class="cols">'
        + _number_field("seed の開始値", "evolution.seed_base", values["evolution.seed_base"])
        + _number_field("GA の乱数種", "evolution.ga_seed", values["evolution.ga_seed"])
        + "</div><p class=\"muted\">並列数は ⚙ 全体設定の「計算」で指定します。</p>",
    )

    section5b = _section(
        "時間の上限", "上限に達すると、その時点までの結果で打ち切ります。",
        '<div class="cols">'
        + _number_field(
            "実行全体", "execution_limits.wall_seconds", values["execution_limits.wall_seconds"],
            unit="秒", min_value=1,
        )
        + "</div>",
    )

    preview_button = "" if duplicate else '<button type="button" data-action="preview">検証する</button>'
    save_label = "複製として保存" if duplicate else "新しい版として保存"
    parent_attr = f' data-parent="{_escape(parent_config_id)}"' if duplicate else ""
    summary = (
        f'<b>{_escape(values["project_id"])}</b> × {_escape(values["template_id"])} ・ '
        f'{gens} 世代 × {population} 個体 × {seeds} seed'
    )
    return (
        f'<form data-wb="config-form"{parent_attr} class="cfg-form">'
        '<p class="form-error" data-form-error role="alert"></p>'
        + section1 + section2 + section3 + section4
        + '<details class="cfg-adv"><summary>詳細設定 '
          '<small>乱数・並列・時間上限。通常は変更不要。</small></summary>'
        + section5a + section5b
        + '</details>'
        + '<div data-preview></div>'
        + '<div class="form-actions">'
        + f'<span class="form-summary" data-summary>{summary}</span>'
        + preview_button
        + f'<button type="submit" class="button-primary is-confirm">{_escape(save_label)}</button>'
        + '</div>'
        + "</form>"
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


def render_config_detail(config, control):
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
        ("並列数", "⚙ 全体設定に従う"),
        ("保存方針", _escape(ev["keep"])),
        ("世界の拡張", _escape(_world_expansion_label(ev.get("world_expansion", "off")))),
        ("前の実験から引き継ぐ", _escape(_seed_genomes_label(ev.get("seed_genomes")))),
        ("世界を育てる", _escape(_growth_label(config.get("growth")))),
        ("共進化", _escape(ev["coevolve"])),
        ("メタ進化", _escape(ev["meta_evolution"])),
        ("説明記録", _escape(ev["record_explanations"])),
        ("結末", _escape(ending_text)),
        ("実行時間上限（秒）", _escape(config["execution_limits"]["wall_seconds"])),
        ("合理性 κ", _escape(_rationality_summary(_frozen_template_dir(control, config), ev))),
        ("道筋 ρ", _escape(_route_rho_summary(ev))),
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
        + '<section class="card"><h2>来歴</h2>' + provenance_section + "</section>"
        + actions
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
        actions.append(f'<a href="/jobs/{_url(record["job_id"])}">詳細を見る</a>')
    elif record["state"] == "legacy":
        actions.append(f'<a href="/exp/{_url(record["experiment_name"])}/monitor">詳細を見る</a>')
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


def _job_shell_open(job, *, extra_attrs=""):
    """The <section data-wb="job" ...> root tag shared by every processing
    page (WB-UI-007/015 job polling attributes). Split out of _job_shell so
    render_run_page (WB-UI-017) can wrap its own body in the same root
    without also getting _job_shell's fixed state-badge/eta/cancel/tail-link
    layout, which the run page places differently.
    """
    state = job.get("state")
    terminal = state in TERMINAL
    return (
        f'<section data-wb="job" data-job-id="{_escape(job.get("job_id"))}" data-poll="1" '
        f'data-terminal="{"true" if terminal else "false"}" '
        f'data-terminal-states="{_escape(TERMINAL_JSON)}" '
        f'data-state-labels="{_escape(STATE_LABELS_JSON)}" '
        f'data-phase-labels="{_escape(PHASE_LABELS_JSON)}"{extra_attrs}>'
    )


def _job_shell(job, body_parts, *, extra_attrs=""):
    """Common processing-page chrome shared by generation jobs
    (output_pages.render_generation_job): state badge, cancel button and tail
    links, wrapped in _job_shell_open's root. Only the kind-specific body
    (including where it places _connection_warnings()) differs between
    callers. `extra_attrs` lets a caller add its own data-* attributes to the
    root <section> (e.g. the generation job page's data-entry-labels for
    translating counts in JS).
    """
    state = job.get("state")
    terminal = state in TERMINAL
    parts = [
        _job_shell_open(job, extra_attrs=extra_attrs),
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
    tail_links = ['<a href="/history">実行履歴へ</a>']
    config_id = job.get("config_id")
    if config_id:
        tail_links.append(f'<a href="/configs/{_url(config_id)}">設定</a>')
    parts.append(f'<p class="actions">{"".join(tail_links)}</p>')
    parts.append("</section>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Run page (WB-UI-017): "設定 -> 実行 -> 生まれつつあるもの" in one screen.
# render_run_page(view) is a pure function of the dict _run_view() builds;
# every job_store/catalog lookup happens in _run_view/_live_map, never here.
# --------------------------------------------------------------------------

RUN_GLOSSARY_KEYS = (
    "cells", "quality", "reach", "generation", "seed", "job_state", "phase", "publication_revision",
)


def _new_config_href(world):
    href = f'/configs/new?project={_url(world["id"])}'
    if world.get("genre"):
        href += f'&template={_url(world["genre"])}'
    return href


def _run_config_picker(world, configs, selected):
    selected_id = selected["config_id"] if selected else None
    options = "".join(
        f'<option value="{_escape(c["config_id"])}"{" selected" if c["config_id"] == selected_id else ""}>'
        f'{_escape(c["label"])}</option>'
        for c in configs
    )
    return (
        '<form method="get" action="/jobs" data-wb="run-config">'
        f'<input type="hidden" name="world" value="{_escape(world["id"])}">'
        '<label class="run-pick"><span>実行設定</span>'
        f'<select name="config">{options}</select></label>'
        '<noscript><button type="submit" class="button">切り替え</button></noscript>'
        "</form>"
    )


def _config_aux_links(config, world):
    return (
        '<p class="actions">'
        f'<a class="button" href="/configs/{_url(config["config_id"])}">編集</a>'
        f'<a class="button" href="/configs/new?from={_url(config["config_id"])}">複製して調整</a>'
        f'<a class="button" href="{_escape(_new_config_href(world))}">＋ 新しく作る</a>'
        "</p>"
    )


def _run_plan(config, estimate, control, *, open_detail=False):
    ev = config["evolution"]
    preview = config["preview"]
    coevolve = "あり" if ev.get("coevolve") else "なし"
    meta = "あり" if ev.get("meta_evolution") else "なし"
    detail = _dl([
        ("評価する個体 / seed",
         f'{_escape(preview["planned_individual_evaluations"])} / {_escape(preview["planned_seed_evaluations"])}'),
        ("保存方針", _escape(ev["keep"])),
        ("世界の拡張", _escape(_world_expansion_label(ev.get("world_expansion", "off")))),
        ("前の実験から引き継ぐ", _escape(_seed_genomes_label(ev.get("seed_genomes")))),
        ("世界を育てる", _escape(_growth_label(config.get("growth")))),
        ("共進化 / メタ進化", f"{coevolve} / {meta}"),
        ("合理性 κ", _escape(_rationality_summary(_frozen_template_dir(control, config), ev))),
        ("道筋 ρ", _escape(_route_rho_summary(ev))),
    ])
    # A running job's page reloads on every publication_revision change
    # (workbench.js's poll loop), which would otherwise re-collapse this
    # <details> each time -- open it by default once a job exists, since the
    # config is fixed at that point and this is the only place to see it.
    open_attr = " open" if open_detail else ""
    return (
        '<div class="run-plan">'
        f'<p class="run-size"><b>{_escape(ev["generations"])}</b> 世代 × '
        f'<b>{_escape(ev["population"])}</b> 個体 × <b>{_escape(ev["seeds"])}</b> seed</p>'
        f'<p class="run-eta"><span class="muted">所要時間の目安</span> {estimate}</p>'
        f'<details{open_attr}><summary>詳細（評価数・保存方針・共進化）</summary>{detail}</details>'
        "</div>"
    )


def _start_cta(config, request_id):
    return (
        f'<div data-wb="start" data-config-id="{_escape(config["config_id"])}" '
        f'data-request-id="{_escape(request_id)}">'
        '<p class="form-error" data-form-error role="alert"></p>'
        '<form><button type="submit" class="button primary">この設定で GA を回す</button></form>'
        "</div>"
    )


def _blocking_notice(blocking_job):
    world_name = blocking_job.get("_blocking_world_name") or "他の世界"
    job_id = blocking_job.get("job_id")
    link = f' <a href="/jobs/{_url(job_id)}">進捗を見る →</a>' if job_id else ""
    return f'<p class="warning">{_escape(world_name)} を実行中です。完了まで待ってください。{link}</p>'


def _cancel_controls(job):
    state = job.get("state")
    disabled = " disabled" if state == "stopping" else ""
    label = "停止処理中（猶予後に強制終了）" if state == "stopping" else "停止"
    return (
        f'<button type="button" data-action="cancel"{disabled}>{_escape(label)}</button>'
        '<span data-cancel-status></span>'
    )


def _run_terminal_message(job):
    state = job.get("state")
    if state in ("succeeded", "partial"):
        return ""
    code = (job.get("error") or {}).get("code")
    if code is None and state == "cancelled":
        # A user-requested stop is not an error; say so instead of "エラー: None".
        return ('<p class="warning">利用者の停止要求により停止しました</p>'
                '<p>同じ設定で新しく実行できます</p>')
    message, next_step = ERROR_MESSAGES.get(
        code, ("エラー情報がありません" if code is None else f"エラー: {code}", ""))
    return f'<p class="error">{_escape(message)}</p><p>{_escape(next_step)}</p>'


def _run_prep(view):
    world, job, config, configs = view["world"], view["job"], view["config"], view["configs"]
    poll_line = ""
    if job is None:
        badge = '<span class="state-badge state-idle">待機中</span>'
    else:
        badge = state_badge(job["state"])
        # updated-at/delta: the WB-UI-015 "最終更新 HH:MM:SS" + what-moved-since
        # summary applyJob() already fills in on every poll. Kept out of the
        # <h2> (only the badge lives there) so the heading's accessible name
        # doesn't change on every poll tick.
        poll_line = (
            '<p class="muted"><span data-field="updated-at"></span> '
            '<span data-field="delta"></span></p>'
        )
    # The state badge lives inside the heading so "設定 待機中" reads as one row.
    parts = [f"<h2>設定 {badge}</h2>", poll_line]
    if not configs:
        if job is not None:
            parts.append("<p>このジョブの設定は削除されています。</p>")
            return "".join(parts)
        parts.append("<p>この世界の実行設定がまだありません。</p>")
        parts.append(
            f'<p><a class="button primary" href="{_escape(_new_config_href(world))}">新しく作る →</a></p>'
        )
        return "".join(parts)
    if job is None:
        parts.append(_run_config_picker(world, configs, config))
        parts.append(_config_aux_links(config, world))
        parts.append(_run_plan(config, view["estimate"], view["control"]))
        if view["blocking_job"] is not None:
            parts.append(_blocking_notice(view["blocking_job"]))
        else:
            parts.append(_start_cta(config, view["request_id"]))
        parts.append('<p class="muted">GA は LLM を呼び出しません。</p>')
    else:
        parts.append(f'<p>設定: <a href="/configs/{_url(config["config_id"])}">{_escape(config["label"])}</a></p>')
        parts.append(_run_plan(config, view["estimate"], view["control"], open_detail=True))
        if job["state"] in RUNNING_STATES:
            parts.append(_cancel_controls(job))
            parts.append('<p class="muted">停止すると、閉じた世代までの結果は残ります。</p>')
        else:
            parts.append(_run_terminal_message(job))
            retry_href = f'/jobs?world={_url(world["id"])}&config={_url(config["config_id"])}'
            parts.append(
                f'<p><a class="button" href="{_escape(retry_href)}">同じ設定でもう一度回す</a></p>'
            )
    return "".join(parts)


def _pbar(label, completed_field, total_field, completed, total, *, aria):
    max_value = int(total) if total else 1
    value = int(completed or 0)
    return (
        '<div class="pbar">'
        f'<label>{_escape(label)}</label>'
        f'<progress aria-label="{_escape(aria)}" value="{value}" max="{max_value}"></progress>'
        f'<span><span data-field="{completed_field}">{_escape(completed) if completed is not None else 0}</span>/'
        f'<span data-field="{total_field}">{_escape(total) if total is not None else "—"}</span></span>'
        "</div>"
    )


def _run_detail(job, progress):
    terminal = job.get("state") in TERMINAL
    parts = ['<details class="run-detail"><summary>詳細</summary>']
    active_seeds = progress.get("active_seeds")
    if active_seeds is not None:
        note = "未完了seedの記録（生存プロセス数ではない）" if terminal else "実行中seed数"
        parts.append(f'<p>{_escape(note)}: <span data-field="active_seeds">{len(active_seeds)}</span></p>')
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
    parts.append("</details>")
    return "".join(parts)


def _action_share_label(key):
    """"category/verb/role" -> a display label that keeps role/verb pairs
    like give_item/ally vs give_item/hostile from colliding on the same verb
    text (WB-LINEAGE-001 review fix)."""
    parts = key.split("/")
    verb = explanation_ui.verb_label(parts[1])
    role = parts[2] if len(parts) > 2 else "none"
    if role == "none":
        return verb
    return f"{verb}→{world_graph._ROLE_LABELS.get(role, role)}"


def _generation_trend_table(generations):
    """WB-LINEAGE-001: one row per closed generation, so "what changed as
    generations passed" survives past the last elite. Server-rendered only --
    the run page's full reload on each new publication (poll() in
    workbench.js) already refreshes this along with the QD map, so no JS
    patch path is needed here."""
    if not generations or "action_share" not in generations[0]:
        return ""
    rows = []
    previous_share = None
    for gen in generations:
        reach = gen.get("reach_rate")
        reach_text = f"{round(reach * 100)}%" if isinstance(reach, (int, float)) else "—"
        allies = gen.get("allies_mean_at_contest")
        allies_text = f"{allies:.1f}" if isinstance(allies, (int, float)) else "—"
        share = gen.get("action_share") or {}
        moved = "—"
        if previous_share is not None:
            candidates = []
            for key in sorted(set(previous_share) | set(share)):
                before = previous_share.get(key, 0.0)
                after = share.get(key, 0.0)
                diff = abs(after - before)
                if diff >= 0.10:
                    candidates.append((key, before, after, diff))
            candidates.sort(key=lambda item: -item[3])
            pieces = [
                f"{_action_share_label(key)} "
                f"{round(before * 100)}%→{round(after * 100)}%"
                for key, before, after, _ in candidates[:3]
            ]
            if pieces:
                moved = " / ".join(pieces)
        rows.append(
            f'<tr><td>g{_escape(gen.get("generation"))}</td>'
            f"<td>{_escape(reach_text)}</td>"
            f"<td>{_escape(allies_text)}</td>"
            f"<td>{_escape(moved)}</td></tr>"
        )
        previous_share = share
    return (
        "<h3>世代の推移</h3>"
        '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
        "<th>世代</th><th>到達率</th><th>仲間</th><th>動いた行動</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _rationality_totals(generations):
    """Fold a job's per-generation rationality_* summary fields (WB-JEV-001's
    gapengine.evolve, only present on kappa>0 runs) into the progress panel's
    one-line total (WB-JEV-002). judge_calls/thermal_wait/budget_exhausted/
    judge_disabled accumulate across generations; table_size is already a
    running total by construction (RationalityTable.load(...) at that point),
    so the latest generation's own value is the right one to show."""
    if not generations or "rationality_judge_calls" not in generations[-1]:
        return None
    return {
        "judge_calls": sum(int(g.get("rationality_judge_calls", 0)) for g in generations),
        "table_size": generations[-1].get("rationality_table_size", 0),
        "thermal_wait_seconds": sum(
            float(g.get("rationality_thermal_wait_seconds", 0.0)) for g in generations),
        "budget_exhausted_runs": sum(
            int(g.get("rationality_budget_exhausted_runs", 0)) for g in generations),
        "judge_disabled_runs": sum(
            int(g.get("rationality_judge_disabled_runs", 0)) for g in generations),
    }


def _rationality_progress_line(totals):
    line = (
        '<p class="run-rationality">合理性: '
        f'判定コール {_escape(totals["judge_calls"])} 回（累計） ・ '
        f'表サイズ {_escape(totals["table_size"])} ・ '
        f'熱待機 {_escape(round(totals["thermal_wait_seconds"]))} 秒 ・ '
        f'予算切れ {_escape(totals["budget_exhausted_runs"])} 件</p>'
    )
    if totals["judge_disabled_runs"] > 0:
        line += '<p class="warning">判定器が途中で使えなくなり、以降は合理性が効いていません。</p>'
    return line


def _run_vessel_progress(view):
    job, config = view["job"], view["config"]
    ghost = job is None
    progress = (job.get("progress") or {}) if job is not None else {}
    if job is None:
        sub = "開始すると埋まります"
    else:
        elapsed = _elapsed_seconds(job, progress)
        sub = (
            '経過 <span data-field="elapsed_seconds">'
            f'{_escape(elapsed) if elapsed is not None else ""}</span> 秒 · '
            '完了予定 <span data-field="eta">—</span>'
        )
    parts = [f'<section class="vbox{" ghost" if ghost else ""}" data-vessel="progress">']
    parts.append(f'<div class="vhead"><h2>進み具合</h2><span class="vhead-sub">{sub}</span></div>')
    if job is not None:
        parts.extend(_connection_warnings(job.get("reconciliation")))
    planned_individual = config["preview"]["planned_individual_evaluations"] if config else None
    planned_seed = config["preview"]["planned_seed_evaluations"] if config else None
    gen_total = progress.get("total_generations") if job is not None else (
        config["evolution"]["generations"] if config else None)
    gen_completed = progress.get("completed_generations") if job is not None else 0
    ind_total = progress.get("total_individuals") if job is not None else planned_individual
    ind_completed = progress.get("completed_individuals") if job is not None else 0
    seed_total = progress.get("total_seeds") if job is not None else planned_seed
    seed_completed = progress.get("completed_seeds") if job is not None else 0
    parts.append(_pbar("完了世代", "completed_generations", "total_generations",
                        gen_completed, gen_total, aria="完了世代"))
    parts.append(_pbar("評価済み個体", "completed_individuals", "total_individuals",
                        ind_completed, ind_total, aria="評価済み個体"))
    parts.append(_pbar("評価済みseed", "completed_seeds", "total_seeds",
                        seed_completed, seed_total, aria="評価済みseed"))
    phase_label = PHASE_LABELS.get(job.get("phase"), job.get("phase")) if job is not None else "—"
    parts.append(f'<p class="run-phase">段階: <span data-field="phase">{_escape(phase_label)}</span></p>')
    if job is not None:
        parts.append(_run_detail(job, progress))
        live = view.get("live")
        generations = (live or {}).get("generations") or []
        totals = _rationality_totals(generations)
        if totals is not None:
            parts.append(_rationality_progress_line(totals))
        parts.append(_generation_trend_table(generations))
    parts.append("</section>")
    return "".join(parts)


def _qd_grade(quality):
    if not isinstance(quality, (int, float)):
        return "f1"
    if quality >= 0.75:
        return "f4"
    if quality >= 0.5:
        return "f3"
    if quality >= 0.25:
        return "f2"
    return "f1"


def _qd_table(categories, bins, live):
    cells = (live or {}).get("cells") or {}
    headings = "".join(f'<th scope="col">{_escape(b)}</th>' for b in bins)
    rows = []
    for category in categories:
        columns = []
        for bin_name in bins:
            cell = cells.get(f"{category}|{bin_name}")
            if cell is None:
                columns.append('<td class="qd-cell empty"></td>')
            else:
                quality = cell.get("quality")
                text = f"{quality:.2f}" if isinstance(quality, (int, float)) else "—"
                new_cls = " new" if cell.get("new") else ""
                columns.append(f'<td class="qd-cell {_qd_grade(quality)}{new_cls}">{_escape(text)}</td>')
        rows.append(f'<tr><th scope="row">{_escape(category)}</th>{"".join(columns)}</tr>')
    return (
        '<div class="grid-wrap"><table class="qd-map" aria-label="QD 地図">'
        f'<thead><tr><th scope="col"></th>{headings}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="qd-cap">縦: 行動カテゴリ · 横: 揺らぎ（volatility）。濃さ = そのマスの代表の質。</p>'
    )


def _qd_delta(occupied, quality, reach):
    # occupied_cells is len(archive.cells) -- always an int, never dropped by
    # _live_map.series(); quality/reach can be None on empty-archive generations.
    n = len(occupied)
    if n == 0:
        return '<p class="qd-delta">世代が閉じるごとに 1 行ずつ増えます。</p>'
    if n == 1:
        occ = occupied[0] if occupied else 0
        q = quality[0] if quality else 0.0
        r = round((reach[0] if reach else 0.0) * 100)
        return f'<p class="qd-delta">第 1 世代: 占有 {_escape(occ)} · q̄ {q:.2f} · 到達率 {r}%</p>'
    occ_diff = occupied[-1] - occupied[-2] if len(occupied) >= 2 else 0
    q_diff = quality[-1] - quality[-2] if len(quality) >= 2 else 0.0
    reach_diff = round((reach[-1] - reach[-2]) * 100) if len(reach) >= 2 else 0
    return (
        f'<p class="qd-delta">第 {n} 世代: 占有 {occ_diff:+d} · '
        f'q̄ {q_diff:+.2f} · 到達率 {reach_diff:+d}pt</p>'
    )


def _qd_metrics(live, grid_size):
    series = (live or {}).get("series") or {}
    occupied = series.get("occupied") or []
    quality = series.get("quality") or []
    reach = series.get("reach") or []
    occ_text = _escape(occupied[-1]) if occupied else "—"
    q_text = f"{quality[-1]:.2f}" if quality else "—"
    reach_text = f"{round(reach[-1] * 100)}" if reach else "—"
    tiles = (
        '<div class="metrics">'
        f'<div class="qd-metric">{pages.term("cells", "占有")} '
        f'<b data-field="metric_occupied">{occ_text}</b><small>/ {grid_size}</small>'
        f'{pages.sparkline(occupied)}</div>'
        f'<div class="qd-metric">{pages.term("quality", "q̄")} '
        f'<b data-field="metric_quality">{_escape(q_text)}</b>'
        f'{pages.sparkline(quality)}</div>'
        f'<div class="qd-metric">{pages.term("reach", "到達率")} '
        f'<b data-field="metric_reach">{_escape(reach_text)}</b><small>%</small>'
        f'{pages.sparkline(reach)}</div>'
        "</div>"
    )
    return tiles + _qd_delta(occupied, quality, reach)


def _run_vessel_map(view):
    job, live = view["job"], view["live"]
    categories, bins = view["axes"]
    grid_size = len(categories) * len(bins)
    revision = live["revision"] if live else None
    if job is None:
        sub = f"この設定が埋めうる {grid_size} マス"
    elif revision is None:
        sub = "まだ公開版はありません"
    elif job["state"] not in TERMINAL:
        sub = f"第 {revision} 世代までの公開版"
    else:
        sub = f"最終公開版 (revision {revision})"
    parts = [f'<section class="vbox{" ghost" if job is None else ""}" data-vessel="map">']
    parts.append(f'<div class="vhead"><h2>生まれつつあるもの</h2><span class="vhead-sub">{_escape(sub)}</span></div>')
    parts.append(_qd_table(categories, bins, live))
    parts.append(_qd_metrics(live, grid_size))
    parts.append("</section>")
    return "".join(parts)


def _exit_card(label, description, href):
    if href:
        return (
            f'<a class="exit on" href="{href}"><strong>{_escape(label)}</strong>'
            f'<span>{_escape(description)}</span></a>'
        )
    return f'<span class="exit off"><strong>{_escape(label)}</strong><span>{_escape(description)}</span></span>'


def _run_vessel_exits(view):
    job, run_name, live = view["job"], view["run_name"], view["live"]
    state = job["state"] if job is not None else None
    ready = state in ("succeeded", "partial") and run_name is not None
    # A broken/unreadable publication hides the map, not the link to it.
    partial_ok = (
        not ready and job is not None and run_name is not None
        and job.get("publication_revision") is not None
    )
    sifting_on = ready or partial_ok
    if job is None:
        sub = "実行が完了すると開きます"
    elif ready:
        sub = "次の工程へ"
    elif partial_ok:
        sub = "途中まで確定した結果があります"
    else:
        sub = "実行が完了すると開きます"
    run_id = job.get("run_id") if job is not None else None
    sifting_href = f"/exp/{_url(run_name)}" if sifting_on else None
    stage_href = f"/outputs?run={_url(run_id)}" if ready and run_id else None
    parts = [f'<section class="vbox{" ghost" if job is None else ""}" data-vessel="exits">']
    parts.append(f'<div class="vhead"><h2>終わったら</h2><span class="vhead-sub">{_escape(sub)}</span></div>')
    parts.append(_exit_card("3 Sifting で候補を選ぶ →", "公開版の候補を並べ、上映する個体を決める", sifting_href))
    parts.append(_exit_card("4 上映を生成する →", "選んだ候補からあらすじ・本文を作る", stage_href))
    if sifting_on and run_id:
        parts.append(f'<p class="actions"><a href="/runs/{_url(run_id)}/candidates">候補一覧</a>'
                     + (f'<a href="/exp/{_url(run_name)}/river">系譜の川</a>' if run_name else "") + "</p>")
    parts.append("</section>")
    return "".join(parts)


def render_run_page(view):
    job = view["job"]
    body = (
        _run_sub(view)
        + '<div class="run-layout">'
        + f'<aside class="run-prep card">{_run_prep(view)}</aside>'
        + '<div class="run-vessel">'
        + _run_vessel_progress(view)
        + _run_vessel_map(view)
        + _run_vessel_exits(view)
        + "</div></div>"
        + pages.glossary(RUN_GLOSSARY_KEYS)
    )
    if job is not None:
        revision = job.get("publication_revision")
        extra_attrs = f' data-revision="{_escape(revision) if revision is not None else ""}"'
        body = _job_shell_open(job, extra_attrs=extra_attrs) + body + "</section>"
    return body


def _run_sub(view):
    world, config = view["world"], view["config"]
    genre = world.get("genre") or "不明"
    if config is not None:
        ev = config["evolution"]
        ending = ", ".join(ev["target_ending"]) if ev.get("target_ending") else "世界の既定"
    else:
        ending = "世界の既定"
    return (
        '<p class="run-sub">'
        f'{_escape(genre)} · 結末: {_escape(ending)} '
        f'<a class="history-link" href="/history">この世界の過去の実行 ({view["history_count"]}) →</a>'
        "</p>"
    )


def _estimate(jobs, configs_by_id, world_id, config):
    if config is None:
        return "—"
    candidates = [
        j for j in jobs
        if j.get("kind", "evolve") == "evolve"
        and j.get("state") in ("succeeded", "partial")
        and configs_by_id.get(j.get("config_id"), {}).get("project_id") == world_id
        and (j.get("progress") or {}).get("elapsed_seconds") is not None
        and (j.get("progress") or {}).get("total_seeds")
    ]
    if not candidates:
        return "—"
    latest = max(candidates, key=lambda j: j["created_at"])
    progress = latest["progress"]
    elapsed = progress["elapsed_seconds"]
    total_seeds = progress["total_seeds"]
    per_seed = elapsed / total_seeds
    planned = config["preview"]["planned_seed_evaluations"]
    seconds = per_seed * planned
    text = f"約{round(seconds)}秒" if seconds < 60 else f"約{math.ceil(seconds / 60)}分"
    return f'{text} <span class="muted">(前回 {elapsed:.0f}秒)</span>'


def _live_map(handler, job):
    if job is None or job.get("run_id") is None or job.get("publication_revision") is None:
        return None
    catalog = handler.repository.catalog
    if catalog is None:
        return None
    try:
        snapshot = catalog.snapshot(job["run_id"], observe=False)
    except (ConfigError, FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        return None
    archive_cells = (snapshot.get("archive") or {}).get("cells") or {}
    generations = snapshot.get("summary", {}).get("generations") or []
    revision = snapshot.get("revision")
    latest_generation = revision - 1 if revision is not None else None
    cells = {}
    for key, elite in archive_cells.items():
        if not isinstance(elite, dict):
            continue
        quality = elite.get("quality")
        cells[key] = {
            "quality": float(quality) if isinstance(quality, (int, float)) else None,
            "new": elite.get("generation") == latest_generation,
        }

    def series(field):
        return [g.get(field) for g in generations if isinstance(g, dict) and g.get(field) is not None]

    return {
        "revision": revision,
        "cells": cells,
        "generations": generations,
        "series": {
            "occupied": series("occupied_cells"),
            "quality": series("average_archive_quality"),
            "reach": series("reach_rate"),
            "dissimilarity": series("archive_dissimilarity"),
        },
    }


def _history_records(handler):
    """catalog.history() once per request (it walks the runs directory); an
    unavailable/broken catalog reads as an empty history."""
    catalog = handler.repository.catalog
    if catalog is None:
        return []
    try:
        return catalog.history()
    except (ConfigError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def _resolve_run_name(handler, catalog_run_id, *, records=None):
    if catalog_run_id is None:
        return None
    if records is None:
        records = _history_records(handler)
    match = next((r for r in records if r["run_id"] == catalog_run_id), None)
    return match["experiment_name"] if match is not None else None


def _run_view(handler, *, world_id=None, config_id=None, job=None):
    job_store = _job_store(handler)
    if job_store is None:
        return None
    try:
        configs = job_store.configs.list()
        jobs = job_store.list()
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        configs, jobs = [], []
    configs_by_id = {c["config_id"]: c for c in configs}

    if job is not None:
        owner = configs_by_id.get(job.get("config_id"))
        world_id = owner["project_id"] if owner is not None else None
        if world_id is None:
            # The job's config was removed from control/configs: nothing to
            # scope to, so show the job under an unnamed world (no picker, no
            # "新しく作る" link into a world that does not exist).
            world_id = ""

    if world_id is None:
        pin = data.pinned_target(job_store, configs=configs, jobs=jobs)
        if pin is None:
            return None
        world_id = pin["world"]["id"]

    library_match = next((w for w in pages._library_worlds(job_store) if w["id"] == world_id), None)
    world = {
        "id": world_id,
        "name": (library_match["name"] if library_match else None) or world_id or "不明な世界",
        "genre": library_match.get("genre") if library_match else None,
    }

    world_configs = sorted(
        (c for c in configs if c["project_id"] == world_id),
        key=lambda c: c["created_at"], reverse=True,
    )

    running_evolve = next(
        (j for j in jobs
         if j.get("state") in RUNNING_STATES and j.get("config_id") in configs_by_id
         and j.get("kind", "evolve") == "evolve"
         and configs_by_id[j["config_id"]]["project_id"] == world_id),
        None,
    )
    if job is None:
        job = running_evolve

    blocking_job = None
    if job is None:
        blocking_job = next(
            (j for j in jobs if j.get("state") in RUNNING_STATES and j.get("config_id") in configs_by_id),
            None,
        )
        if blocking_job is not None:
            b_config = configs_by_id.get(blocking_job.get("config_id"))
            b_world_id = b_config["project_id"] if b_config else None
            b_match = next((w for w in pages._library_worlds(job_store) if w["id"] == b_world_id), None)
            blocking_job = dict(blocking_job)
            blocking_job["_blocking_world_name"] = (b_match["name"] if b_match else None) or b_world_id or "他の世界"

    if job is not None:
        config = configs_by_id.get(job.get("config_id"))
    elif config_id is not None and any(c["config_id"] == config_id for c in world_configs):
        config = configs_by_id[config_id]
    elif world_configs:
        config = world_configs[0]
    else:
        config = None

    records = _history_records(handler)
    run_name = _resolve_run_name(handler, job.get("run_id"), records=records) if job is not None else None
    live = _live_map(handler, job)

    template_dir = None
    if config is not None:
        template_dir = job_store.configs.repo / "templates" / config["template_id"]
    elif world.get("genre"):
        template_dir = job_store.configs.repo / "templates" / world["genre"]
    axes = data.qd_axes(template_dir)

    estimate = _estimate(jobs, configs_by_id, world_id, config)

    world_config_ids = {c["config_id"] for c in world_configs}
    history_count = sum(1 for r in records if r.get("config_id") in world_config_ids)

    request_id = None if job is not None else "req-" + uuid.uuid4().hex

    return {
        "world": world, "configs": world_configs, "config": config, "job": job,
        "blocking_job": blocking_job, "run_name": run_name, "live": live, "axes": axes,
        "estimate": estimate, "history_count": history_count, "request_id": request_id,
        "control": job_store.configs.control,
    }


def _run_lead(view):
    job = view["job"]
    if job is None:
        return "設定を選んで実行します。右の器は、この設定で何が生まれるかの予告です。", None
    state = job["state"]
    if state in RUNNING_STATES:
        generation = (job.get("progress") or {}).get("completed_generations") or 0
        return f"第 {generation + 1} 世代を評価中です。世代が閉じるたびに右の地図が埋まります。", None
    if state in ("succeeded", "partial"):
        completed = (job.get("progress") or {}).get("completed_generations") or 0
        lead = f"{completed} 世代が終わりました。生まれた候補を Sifting で選びます。"
        run_name = view["run_name"]
        next_action = ("Sifting へ →", f"/exp/{_url(run_name)}") if run_name else None
        return lead, next_action
    return "実行は途中で止まりました。", None


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


SYNOPSIS_SNIPPET_CHARS = 60


def _synopsis_snippet(text):
    if not text:
        return "—"
    flat = " ".join(text.split())
    if not flat:
        return "—"
    return flat if len(flat) <= SYNOPSIS_SNIPPET_CHARS else flat[:SYNOPSIS_SNIPPET_CHARS] + "…"


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

    def value_of(candidate):
        return _sort_value(candidate, key, output_summary)

    by_id = sorted(candidates, key=lambda candidate: candidate["candidate_id"])
    present = [c for c in by_id if value_of(c) is not None]
    missing = [c for c in by_id if value_of(c) is None]
    present.sort(key=value_of, reverse=(direction == "desc"))
    return present + missing


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


def _candidate_row(candidate, run_id, experiment_name, is_representative, running, output_summary,
                    synopsis_text=None):
    cid = candidate["candidate_id"]
    short = _short_id(cid)
    disabled = " disabled" if running else ""
    state_options = "".join(
        f'<option value="{option}"{" selected" if candidate["state"] == option else ""}>'
        f'{_escape(CANDIDATE_STATE_LABELS.get(option, option))}</option>'
        for option in CANDIDATE_STATE_OPTIONS
    )
    checkbox = ""
    if candidate.get("screenable"):
        checkbox_disabled = " disabled" if running else ""
        checkbox = (
            '<label class="candidate-generate-check">'
            f'<input type="checkbox" name="candidate" value="{_escape(cid)}" form="generate-form" '
            f'aria-label="候補 {_escape(short)} を生成対象にする"{checkbox_disabled}>'
            '<span>生成</span></label>'
        )
    quality = candidate.get("quality")
    quality_text = f"{quality:.4f}" if isinstance(quality, (int, float)) else "—"
    synopsis = _synopsis_snippet(synopsis_text)
    reached_text = _reached_text(candidate.get("reached"))
    detail_id = f"detail-{_escape(cid)}"
    links = []
    primary_href = f'/outputs?run={_url(run_id)}'
    primary_label = "作品を見る"
    if is_representative:
        cell = candidate.get("cell_key")
        primary_href = f'/exp/{_url(experiment_name)}/cell/{_url(cell)}'
        primary_label = "物語と根拠を詳しく読む"
        links.append(f'<a href="{primary_href}">格子で見る</a>')
    if candidate["log"]["availability"] == "present":
        raw_href = f'/runs/{_url(run_id)}/candidates/{_url(cid)}/raw'
        if not is_representative:
            primary_href, primary_label = raw_href, "原ログを読む"
        links.append(f'<a href="{raw_href}">原ログ</a>')
    links.append(f'<a href="/outputs?run={_url(run_id)}">作品</a>')

    counts = (output_summary or {}).get(cid, {})
    syn_ok, nar_ok = counts.get("synopsize", 0), counts.get("narrate", 0)
    draft_text = f"あらすじ ok {syn_ok} / 上映 ok {nar_ok}" if (syn_ok or nar_ok) else "—"
    row = (
        f'<tr class="candidate-list-row" data-candidate-id="{_escape(cid)}" '
        f'data-candidate-label="{_escape(short)}" data-title="{_escape(candidate.get("cell_key"))}" '
        f'data-synopsis="{_escape(synopsis)}" data-quality="{_escape(quality_text)}" '
        f'data-reached="{_escape(reached_text)}" data-screenable="{_escape(candidate.get("screenable"))}" '
        f'data-generation="{_escape(candidate.get("generation"))}" data-seed="{_escape(candidate.get("seed"))}" '
        f'data-state="{_escape(candidate["state"])}" data-primary-href="{_escape(primary_href)}" '
        f'data-primary-label="{_escape(primary_label)}">'
        f'<td class="candidate-check">{checkbox}</td>'
        '<td class="candidate-story-cell">'
        f'<button type="button" class="candidate-open" aria-label="候補 {_escape(short)} の概要を見る">'
        f'<span class="candidate-row-title"><small>{_escape(short)}</small><strong>{_escape(candidate.get("cell_key"))}</strong></span>'
        f'<span class="candidate-row-synopsis">{_escape(synopsis)}</span>'
        '<span class="candidate-row-traits">'
        f'<span>品質 {_escape(quality_text)}</span><span>到達 {_escape(reached_text)}</span>'
        f'<span>g{_escape(candidate.get("generation"))}</span></span>'
        '</button></td>'
        f'<td class="candidate-number">{_escape(quality_text)}</td>'
        f'<td class="candidate-reach">{_escape(reached_text)}</td>'
        f'<td class="candidate-row-state"><select data-field="state" class="state-select state-sel-{_escape(candidate["state"])}" '
        f'aria-label="選定状態 {_escape(short)}"{disabled}>{state_options}</select></td>'
        '<td class="candidate-note-save">'
        f'<input data-field="note" aria-label="メモ {_escape(short)}" placeholder="メモ" '
        f'value="{_escape(candidate.get("note", ""))}"{disabled}>'
        f'<button type="button" data-action="save-candidate"{disabled}>保存</button>'
        '<span data-save-status></span></td>'
        '<td class="wb-actions">'
        f'<button type="button" class="row-toggle" aria-expanded="false" aria-controls="{detail_id}">技術詳細</button>'
        '</td></tr>'
    )
    detail = (
        f'<tr id="{detail_id}" class="detail-row" hidden><td colspan="7">'
        '<dl class="metric">'
        f'<dt>{pages.term("candidate_generation", "世代")}</dt><dd>{_escape(candidate.get("generation"))}</dd>'
        f'<dt>{pages.term("individual", "個体")}</dt><dd>{_escape(candidate.get("individual_index"))}</dd>'
        f'<dt>{pages.term("candidate_seed", "seed")}</dt><dd>{_escape(candidate.get("seed"))}</dd>'
        f'<dt>{pages.term("role", "役割")}</dt><dd>{_escape(candidate.get("role"))}</dd>'
        f'<dt>{pages.term("log", "原記録")}</dt><dd>{_escape(AVAILABILITY_LABELS.get(candidate["log"]["availability"], candidate["log"]["availability"]))}</dd>'
        f'<dt>{pages.term("draft", "稿")}</dt><dd>{_escape(draft_text)}</dd>'
        '</dl>'
        f'<p class="detail-links">{" ".join(links)}</p>'
        '</td></tr>'
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
    state_options = options("state", [(s, CANDIDATE_STATE_LABELS.get(s, s)) for s in CANDIDATE_STATE_OPTIONS])
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
                            sort_key=None, sort_dir="asc", running_job_id=None, synopses=None):
    adopted_count = sum(1 for candidate in candidates if candidate["state"] == "adopted")
    state_filter = (query.get("state") or [""])[0]
    def quick_filter(label, value):
        active = state_filter == value
        href = f'/runs/{_url(run_id)}/candidates' + (f'?state={_url(value)}' if value else '')
        return f'<a class="candidate-filter-pill{" is-active" if active else ""}" href="{href}">{label}</a>'

    context = (
        '<div class="candidate-context">'
        f'<p><strong>{_escape(experiment_name)}</strong>'
        f'<span>公開版 {_escape(revision)}</span><span>選定版 {_escape(selection_revision)}</span>'
        f'<span>{len(candidates)}件</span></p>'
        '<nav class="candidate-related" aria-label="関連ページ">'
        f'<a href="/exp/{_url(experiment_name)}">格子</a>'
        + (f'<a href="/configs/{_url(config_id)}">実行設定</a>' if config_id else "")
        + '<a href="/selected">Sifting トレイ</a>'
        + '<a href="/outputs?run=' + _url(run_id) + '">作品</a>'
        + '</nav></div>'
    )
    notices = ""
    if running:
        job_link = f' <a href="/jobs/{_url(running_job_id)}">進捗を見る →</a>' if running_job_id else ""
        notices += f'<p class="warning">実行中のため選定は保存できません{job_link}</p>'
    if representatives_error:
        notices += '<p class="warning">代表セルを解決できないため格子へのリンクは表示しません</p>'
    if output_summary_error:
        notices += '<p class="warning">稿の記録を読み取れないため件数を表示できません</p>'
    if has_adopted is None:
        has_adopted = adopted_count > 0
    generate_form = _generate_form(run_id, has_adopted, running)
    if candidates:
        rows = "".join(
            _candidate_row(
                candidate, run_id, experiment_name,
                candidate["candidate_id"] in representatives,
                running, output_summary,
                synopsis_text=(synopses or {}).get(candidate.get("cell_key")),
            )
            for candidate in candidates
        )
        table = (
            '<div class="candidate-table-scroll"><table id="candidate-table" class="wb-table candidate-table-modern">'
            '<thead><tr><th>生成</th><th>候補</th><th>品質</th><th>到達</th><th>選定状態</th><th>メモ</th><th>操作</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
        )
        toolbar = (
            '<div class="candidate-list-toolbar">'
            + quick_filter("すべて", "") + quick_filter("採用", "adopted") + quick_filter("保留", "held")
            + '<details class="candidate-filters"><summary>詳しい絞り込み</summary>'
            + _candidates_filter_form(run_id, query) + '</details></div>'
        )
        list_region = (
            '<section class="candidate-list-region" aria-label="候補一覧">'
            + toolbar + table + pages.glossary(CANDIDATES_GLOSSARY_KEYS) + '</section>'
        )
        state_options = "".join(
            f'<option value="{option}">{_escape(CANDIDATE_STATE_LABELS.get(option, option))}</option>'
            for option in CANDIDATE_STATE_OPTIONS
        )
        inspector = (
            '<aside class="candidate-inspector candidate-inspector-final" aria-label="選択候補の概要">'
            '<header class="candidate-inspector-head"><div>'
            '<div class="candidate-inspector-kicker"><span data-inspector-id>候補</span>'
            '<span class="state-badge" data-inspector-state-label>未分類</span>'
            '<span>品質 <strong data-inspector-quality>—</strong></span></div>'
            '<h2 data-inspector-title>候補を選択</h2>'
            '<p>物語を先に読み、必要なときだけ選択理由と実験値を確認します。</p>'
            '</div></header>'
            '<div class="candidate-inspector-tabs" role="tablist" aria-label="候補情報">'
            '<button type="button" class="candidate-inspector-tab is-active" data-inspector-tab="story" aria-selected="true">物語</button>'
            '<button type="button" class="candidate-inspector-tab" data-inspector-tab="selection" aria-selected="false">選択とメモ</button>'
            '<button type="button" class="candidate-inspector-tab" data-inspector-tab="data" aria-selected="false">実験データ</button>'
            '</div>'
            '<div class="candidate-inspector-body" aria-live="polite">'
            '<section data-inspector-panel="story"><h3>あらすじ</h3>'
            '<p class="candidate-inspector-lead" data-inspector-synopsis>左の一覧から候補を選択してください。</p>'
            '<a class="candidate-inspector-primary" data-inspector-primary href="#candidate-table">物語と根拠を詳しく読む</a></section>'
            '<section data-inspector-panel="selection" hidden><h3>この候補の扱い</h3>'
            f'<label class="inspector-field">選定状態<select data-inspector-state>{state_options}</select></label>'
            '<label class="inspector-field">メモ<input type="text" data-inspector-note placeholder="判断理由を残す"></label>'
            '<div class="inspector-save-row"><button type="button" class="button primary is-confirm" data-inspector-save>状態とメモを保存</button>'
            '<span data-inspector-save-status></span></div></section>'
            '<section data-inspector-panel="data" hidden><h3>実験データ</h3>'
            '<dl class="candidate-inspector-metrics"><div><dt>品質</dt><dd data-inspector-quality>—</dd></div>'
            '<div><dt>到達</dt><dd data-inspector-reached>—</dd></div>'
            '<div><dt>世代</dt><dd data-inspector-generation>—</dd></div>'
            '<div><dt>seed</dt><dd data-inspector-seed>—</dd></div></dl>'
            '<button type="button" class="button" data-inspector-detail-toggle>技術詳細を一覧に表示</button></section>'
            '</div>'
            '<footer class="candidate-inspector-footer"><div><strong>採用候補 <span data-adopted-count>'
            f'{adopted_count}</span>件</strong><span data-generate-count>生成対象 0件</span></div>'
            '<button type="button" class="button primary is-confirm" data-inspector-adopt>この候補を採用</button></footer>'
            '</aside>'
        )
    else:
        list_region = '<section class="candidate-list-region"><p class="candidate-empty">候補がありません。</p></section>'
        inspector = '<aside class="candidate-inspector candidate-inspector-empty"><h2>候補を待っています</h2><p>実行が完了すると、ここで物語候補を比較できます。</p></aside>'
    return (
        f'<section id="candidates" class="candidate-final" data-wb="candidates" data-run-id="{_escape(run_id)}" '
        f'data-revision="{_escape(selection_revision)}">'
        + context + notices
        + '<div class="candidate-layout candidate-layout-final">' + list_region + inspector + '</div>'
        + '<footer class="candidate-generation-bar">' + generate_form + '</footer>'
        + '</section>'
    )

def _tray_row(entry):
    cid = entry["candidate_id"]
    return (
        f'<tr data-run-id="{_escape(entry["run_id"])}" data-candidate-id="{_escape(cid)}" '
        f'data-revision="{_escape(entry["selection_revision"])}">'
        f'<td title="{_escape(cid)}">{_escape(_short_id(cid))}</td>'
        f'<td>{_escape(entry.get("cell_key"))}</td>'
        f'<td>{_escape(entry.get("generation"))}/{_escape(entry.get("seed"))}</td>'
        f'<td>{selection_state_badge(entry.get("state"))}</td>'
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


def render_output_settings_card(settings_path):
    """WB-UI-021: the single generation-settings source, shown on /configs.

    Backend-specific credentials (api_key/command/base_url/options) never
    reach this HTML -- read_output_settings() already strips them.
    """
    try:
        view = read_output_settings(settings_path)
        current = {"backend": view["backend"], "model": view["model"], "limits": view["limits"]}
        availability = generation_availability(current, settings_path)
    except ConfigError:
        # A broken settings.json must not 500 the whole /configs page --
        # show the same reason text the API/JS would, with no editable form.
        avail_text = GENERATION_REASON_LABELS["settings_unreadable"]
        return (
            '<section class="card" id="output"><h2>文章生成</h2>'
            '<p class="desc">Sifting で経緯を文章にするときの生成先。すべての実行設定に共通です。</p>'
            f'<p data-availability>{_escape(avail_text)}</p>'
            "</section>"
        )
    avail_text = availability_label(availability)
    limits = view["limits"]
    backends_attr = _escape(json.dumps(view["backends"], ensure_ascii=False, sort_keys=True))
    api_key_backends_attr = _escape(json.dumps(sorted(API_KEY_BACKENDS)))
    verified_options = "".join(
        f'<option value="{_escape(model)}">'
        for model in view["backends"][view["backend"]]["verified_models"]
    )
    needs_api_key = view["backend"] in API_KEY_BACKENDS
    api_key_status = "設定済み(変更する場合のみ入力)" if view["backends"][view["backend"]]["has_api_key"] else "未設定"
    form = (
        f'<form data-wb="output-settings" class="cfg-form" data-backends="{backends_attr}" '
        f'data-api-key-backends="{api_key_backends_attr}">'
        '<p class="form-error" data-form-error role="alert"></p>'
        + _described_select_field("生成方式", "backend", GENERATION_BACKEND_OPTIONS, view["backend"])
        + '<div class="field" data-api-key-field'
        + ("" if needs_api_key else " hidden")
        + '>'
        + '<label for="f-api_key">APIキー <span class="key">api_key</span></label>'
        + '<input id="f-api_key" type="password" data-apikey autocomplete="off" placeholder="sk-...">'
        + f'<p class="hint" data-api-key-status>{_escape(api_key_status)}</p>'
        + '<div class="field-actions">'
        + '<button type="button" class="button-secondary" data-wb-save-api-key>キーを保存</button>'
        + '</div>'
        + '</div>'
        + _text_field("モデル", "model", view["model"] or "", placeholder="方式に合わせて明示",
                       list_id="output-model-list")
        + f'<datalist id="output-model-list">{verified_options}</datalist>'
        + '<p class="hint" data-model-hint></p>'
        + '<div class="field-actions">'
        + '<button type="button" class="button-secondary" data-wb-test-model>疎通テスト</button>'
        + '</div>'
        + '<details class="cfg-adv"><summary>上限 <small>時間・回数。通常は変更不要。</small></summary>'
        + '<div class="cols">'
        + _number_field("呼び出し回数", "limits.max_calls", limits["max_calls"], unit="回", min_value=0)
        + _number_field("生成 1 回", "limits.call_timeout_seconds", limits["call_timeout_seconds"], unit="秒", min_value=1)
        + "</div>"
        + '<div class="cols">'
        + _number_field("生成全体", "limits.wall_seconds", limits["wall_seconds"], unit="秒", min_value=1)
        + _number_field(
            "応答の保存上限", "limits.max_saved_response_bytes",
            limits["max_saved_response_bytes"], unit="bytes", min_value=0,
        )
        + "</div></details>"
        + f'<p data-availability>{_escape(avail_text)}</p>'
        + '<div class="form-actions"><button type="submit" class="button-primary is-confirm">保存</button></div>'
        + "</form>"
    )
    return (
        '<section class="card" id="output"><h2>文章生成</h2>'
        '<p class="desc">Sifting で経緯を文章にするときの生成先。すべての実行設定に共通です。</p>'
        + form + "</section>"
    )


def _configs_list(handler):
    from viewer import global_settings
    return global_settings.render(handler)


def _configs_new(handler):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    from execution.library import LibraryStore
    query = _query(handler)
    from_id = query.get("from", [None])[0]
    repo = job_store.configs.repo
    projects = sorted(p.name for p in (repo / "projects").iterdir() if p.is_dir()) if (repo / "projects").is_dir() else []
    templates = sorted(p.name for p in (repo / "templates").iterdir() if p.is_dir()) if (repo / "templates").is_dir() else []
    worlds = LibraryStore(repo).worlds()
    world_genres = {w["id"]: w["genre"] for w in worlds}
    world_names = {w["id"]: (w["name"] or w["id"]) for w in worlds}
    if from_id is not None:
        parent = job_store.configs.get(from_id)
        values = _initial_values(
            label=parent["label"], project_id=parent["project_id"], template_id=parent["template_id"],
            evolution=parent["evolution"], execution_limits=parent["execution_limits"],
            growth=parent.get("growth"),
        )
        rationality_ctx = _rationality_form_context(repo, values["template_id"])
        route_ctx = _route_form_context(repo, values["template_id"])
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
            genre = world_genres.get(project_preset)
            if genre in templates:
                values["template_id"] = genre
        # WB-JEV-002: a brand new form defaults kappa to 0.6 when the genre's
        # judge is actually reachable right now, else 0 -- never touching a
        # duplicate/edit's own saved value (handled above). A judge-enabled
        # default also needs headroom in the wall-clock limit (~90s/run vs.
        # the usual few seconds), so its default rises with it.
        rationality_ctx = _rationality_form_context(repo, values["template_id"])
        if rationality_ctx is not None:
            if rationality_ctx["available"]:
                values["evolution.kappa"] = 0.6
                values["execution_limits.wall_seconds"] = 21600
            else:
                values["evolution.kappa"] = 0
        # WB-ROUTE-001 S4 §2 (design judgment): a brand new form defaults ρ
        # to 1.0 when the genre actually has a route.yaml -- ρ carries no
        # extra compute cost (unlike κ), so there is no reason to default it
        # off. Never touches a duplicate/edit's own saved value (handled
        # above, outside this branch). The engine/CLI/template default stays
        # 0 (None) so existing tests and past runs stay reproducible.
        route_ctx = _route_form_context(repo, values["template_id"])
        if route_ctx is not None:
            values["evolution.route_rho"] = 1.0
        if values["project_id"] and values["template_id"]:
            values["label"] = quick_label(
                world_names.get(values["project_id"], values["project_id"]), values["template_id"])
    from viewer import run_settings
    return run_settings.render(handler, values, projects=projects, templates=templates, worlds=worlds,
                               parent=parent if from_id is not None else None,
                               rationality=rationality_ctx, route=route_ctx)


def _configs_detail(handler, cid):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    config = job_store.configs.get(cid)
    from viewer import run_browse
    return run_browse.conditions(handler, config=config)


def _configs_start(handler, cid):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    config = job_store.configs.get(cid)
    # WB-UI-017: the confirmation page is gone -- /configs/<cid>/start now
    # just redirects straight to the run screen (idle prep, or running/done
    # if a job for this config already exists), with the config preselected.
    location = f'/jobs?world={_url(config["project_id"])}&config={_url(cid)}'
    handler.send_response(HTTPStatus.FOUND)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.end_headers()


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
    document = job_store.configs.duplicate(cid, body["changes"])
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


def _history(handler):
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
    from viewer import run_browse
    return run_browse.history(handler, records, generation_jobs)


def _jobs_list(handler):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="run"))
        return
    query = _query(handler)
    # ?world= arrives from a per-world page's "2 実行" tab (_phase_href) so
    # this scopes to *that* world instead of whichever world's config is
    # newest system-wide -- see data.pinned_target's world_id docstring.
    world_id = query.get("world", [None])[0] or None
    config_id = query.get("config", [None])[0] or None
    view = _run_view(handler, world_id=world_id, config_id=config_id)
    if view is None:
        handler._send_html(pages.document(
            "実行", '<p class="muted">世界を選んでください。</p>',
            phase="run", job_store=job_store,
            lead="世界を選んでください。",
            next_action=("ホームへ →", "/"),
        ))
        return
    job = view["job"]
    if job is not None:
        from viewer import run_workspace
        return run_workspace.render(handler, view)
    from viewer import run_browse
    return run_browse.conditions(handler, view)


def _jobs_detail(handler, jid):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="run"))
        return
    job = job_store.get(jid)
    if job.get("kind") in ("synopsize", "narrate"):
        from viewer.generation_pages import render_job
        return render_job(handler, job)
    if job.get("kind") == "world_patch":
        # No dedicated observation screen yet (stage 3b-3 only adds the job
        # itself) -- the run's own monitor page already has a "世界の需要"
        # tab (viewer/world_demand_view.py) that's the natural place to see
        # this land, same as _configs_start's redirect below.
        location = f'/exp/{_url(job["run_id"])}/monitor?tab=demand'
        handler.send_response(HTTPStatus.FOUND)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.end_headers()
        return
    view = _run_view(handler, job=job)
    from viewer import run_workspace
    return run_workspace.render(handler, view)


def _candidates_list(handler, run_id):
    from viewer import sifting_pages
    return sifting_pages.candidates(handler, run_id)


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
    from viewer import raw_view, raw_pages
    from execution.output_store import verified
    source = raw_view.source_bytes(
        verified(contained(root, relative_path), candidate["source_log_sha256"]),
        relative=relative_path, experiment=experiment_name, cell=candidate.get("cell_key") or "未分類",
        generation=candidate.get("generation"), seed=candidate.get("seed"))
    query = _query(handler)
    options = {key:query.get(key,[default])[0] for key,default in
               (("line",None),("q",""),("kind",""),("person",""),("page",None),("mode","readable"))}
    handler._send_html(raw_pages.render_source(source, **options,
        expected_source=query.get("source",[None])[0], job_store=_job_store(handler), output_run=run_id,
        back_href=f"/runs/{_url(run_id)}/candidates?candidate={_url(candidate_id)}",
        raw_href=f"/runs/{_url(run_id)}/candidates/{_url(candidate_id)}/raw"))



def _tray(handler):
    from viewer import sifting_pages
    return sifting_pages.tray(handler)


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
    if head == "history" and len(parts) == 1:
        return _history, ()
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
        if method == "GET" and not parts[0] == "api":
            raise
        job_api.send_error(handler, error)
    except FileNotFoundError:
        if method == "GET" and parts[0] != "api":
            raise data.MissingResource("保存された記録が見つかりません")
        job_api.send_error(handler, ConfigError("resource", "公開済み記録がありません", code="not_found"))
    except (data.BadRequest, data.ForbiddenPath, data.MissingResource) as error:
        if method == "GET" and parts[0] != "api":
            raise
        job_api.send_data_error(handler, error)
    except (OSError, ValueError, TypeError, KeyError):
        if method == "GET" and parts[0] != "api":
            raise OSError("保存された情報を読み取れません")
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
            "code": "storage_error", "message": "保存済み記録を処理できません",
            "field_errors": {}, "retryable": False, "current_revision": None,
        })
    return True
