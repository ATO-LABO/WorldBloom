"""HTML views for LLM-generated outputs: request confirmation, job monitor,
and the output list/detail (WB-UI-008).

Every state change goes through the existing JSON APIs (execution/jobs.py via
job_api.py: POST /api/jobs, POST /api/outputs/{id}/recover). This module only
renders HTML and reads durable output records directly (OutputStore) for
prompt/body text. The only LLM call anywhere in this module's reach is the
user's own click of [この内容で生成を開始] on the confirmation page (handled by
workbench.js's initGenerate(), which POSTs the server-rendered request
verbatim) -- list/detail/polling/reload never call POST /api/jobs themselves.
"""
from __future__ import annotations

from http import HTTPStatus
import json
import uuid

from execution.output_settings import current_generation
from execution.output_store import OutputStore, verified
from execution.provenance import ConfigError, contained, read_json
from execution.worker import TERMINAL
from viewer import job_api, pages, workbench_pages, data

_escape = pages._escape
_url = pages._url_segment
_short_id = workbench_pages._short_id
_query = workbench_pages._query
_job_store = workbench_pages._job_store
_guidance_page = workbench_pages._guidance_page
_reached_text = workbench_pages._reached_text

# --------------------------------------------------------------------------
# Display vocabulary (§3.5) -- the module's source of truth for wording.
# --------------------------------------------------------------------------

ENTRY_STATUS_LABELS = {
    "pending": "未開始", "running": "生成中", "ok": "本文あり", "error": "失敗",
    "prompt_only": "プロンプトのみ", "unknown": "結果不明",
    "skipped_limit": "上限で未開始", "skipped_cancelled": "停止で未開始",
    "skipped_interrupted": "中断で未開始",
}

COMPLETION_LABELS = {
    "generated": "全件生成", "prompt_only": "プロンプト保存のみ（本文未生成）",
    "mixed": "本文とプロンプトの混在", "partial": "一部成功",
    "partial_unknown": "一部成功（結果不明あり）", "unknown": "結果不明（失敗確定ではない）",
    "limit_before_start": "呼出し前に上限到達", "error": "失敗",
    "cancelled": "停止", "interrupted": "中断",
}

RETRY_LABELS = {
    "never": "",
    "safe_new_request": "問題を解消してから『未生成・失敗分を生成』で新規要求できます",
    "explicit_confirmation": "結果不明です。二重生成の可能性を確認したうえで『結果不明の候補を確認して再生成』から明示的に再生成してください",
    "local_recovery_only": "保存障害。LLM は再送せず『復旧』で保存済み証跡から復元します",
    "new_budget_request": "上限に達しました。上限を見直した新しい設定版で新規要求してください",
}

KIND_LABELS = {"synopsize": "あらすじ生成", "narrate": "上映生成"}

MODE_LABELS = {
    "missing_or_failed": "過去に成功した候補は対象外",
    "regenerate": "別の稿として保存、過去稿は保持",
}

_TERMINAL_JSON = json.dumps(sorted(TERMINAL), ensure_ascii=False)
_STATUS_LABELS_JSON = json.dumps(ENTRY_STATUS_LABELS, ensure_ascii=False, sort_keys=True)

_RETRY_TARGET_STATUSES = ("skipped_limit", "skipped_cancelled", "skipped_interrupted", "pending")


# --------------------------------------------------------------------------
# Shared read helpers
# --------------------------------------------------------------------------

def run_output_summary(job_store, run_id):
    """Per-candidate ok counts for this run's outputs, for the candidates page's 稿 column.

    A storage error degrades to an empty summary; the candidates page must
    keep working even when the output ledger is damaged.
    """
    if job_store is None:
        return {"error": True, "by_candidate": {}}
    try:
        outputs = job_store.outputs()
    except (ConfigError, OSError, ValueError, KeyError, TypeError, AttributeError):
        # A duck-typed job store without outputs() (older test doubles, or a
        # storage failure) must not take the whole candidates page down.
        return {"error": True, "by_candidate": {}}
    by_candidate = {}
    for output in outputs:
        request = output.get("request")
        if not request or request.get("run_id") != run_id:
            continue
        kind = request.get("kind")
        if kind not in ("synopsize", "narrate"):
            continue
        for entry in output.get("entries", []):
            if entry.get("status") != "ok":
                continue
            bucket = by_candidate.setdefault(entry["candidate_id"], {"synopsize": 0, "narrate": 0})
            bucket[kind] += 1
    return {"error": False, "by_candidate": by_candidate}


def _run_outputs(job_store, run_id, *, kind=None):
    try:
        outputs = job_store.outputs()
    except (ConfigError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    result = []
    for output in outputs:
        request = output.get("request")
        if not request or request.get("run_id") != run_id:
            continue
        if kind is not None and request.get("kind") != kind:
            continue
        result.append(output)
    return result


def _auto_synopsis_refs(job_store, run_id, candidate_ids):
    """The latest ok synopsize entry per candidate (output_id lexicographic order)."""
    pairs = []
    for output in sorted(_run_outputs(job_store, run_id, kind="synopsize"), key=lambda o: o["output_id"]):
        for entry in output.get("entries", []):
            if entry.get("status") == "ok":
                pairs.append((output["output_id"], entry))
    refs = {}
    for output_id, entry in pairs:
        cid = entry.get("candidate_id")
        if cid in candidate_ids:
            refs[cid] = {"output_id": output_id, "attempt_id": entry["attempt_id"],
                         "text_sha256": entry["text_sha256"]}
    return refs


def _prompt_text(store, output_id, candidate_id):
    sink = store.sink(output_id, candidate_id)
    return verified(sink.folder / "prompt.txt", sink.prompt_sha256).decode("utf-8")


# --------------------------------------------------------------------------
# §3.2 -- generation job processing page (shares workbench_pages._job_shell)
# --------------------------------------------------------------------------

def render_generation_job(job):
    output_id = job.get("output_id")
    progress = job.get("progress") or {}
    kind = job.get("kind")
    counts = job.get("counts") or progress.get("counts") or {}
    completion_kind = job.get("completion_kind")
    body = [
        f'<p>種別: {_escape(KIND_LABELS.get(kind, kind))}</p>',
        f'<p data-field="phase">{_escape(workbench_pages.PHASE_LABELS.get(job.get("phase"), job.get("phase")))}</p>',
    ]
    body.extend(workbench_pages._connection_warnings(job.get("reconciliation")))
    elapsed = workbench_pages._elapsed_seconds(job, progress)
    body.append(
        '<p>経過秒: '
        f'<span data-field="elapsed_seconds">{_escape(elapsed) if elapsed is not None else ""}</span></p>'
    )
    body.append(f'<p>run: {_escape(job.get("run_id"))}</p>')
    if output_id:
        body.append(f'<p><a href="/outputs/{_url(output_id)}">作品を見る</a></p>')
    total = progress.get("total")
    completed = progress.get("completed")
    if total is not None:
        body.append(
            '<div class="progress-row"><label>完了 '
            f'<span data-field="completed">{_escape(completed)}</span>/'
            f'<span data-field="total">{_escape(total)}</span></label>'
            f'<progress aria-label="完了" value="{int(completed or 0)}" max="{int(total or 1)}"></progress></div>'
        )
    if counts:
        text = " · ".join(f"{ENTRY_STATUS_LABELS.get(status, status)} {n}" for status, n in sorted(counts.items()))
        body.append(f'<p data-field="counts">{_escape(text)}</p>')
    if job.get("state") in TERMINAL and completion_kind:
        body.append(f'<p>{_escape(COMPLETION_LABELS.get(completion_kind, completion_kind))}</p>')
    return workbench_pages._job_shell(
        job, body, extra_attrs=f' data-entry-labels="{_escape(_STATUS_LABELS_JSON)}"',
    )


def render_generation_jobs_section(jobs):
    if not jobs:
        return "<section><h2>生成ジョブ</h2><p>生成ジョブはありません。</p></section>"
    rows = "".join(_generation_job_row(job) for job in jobs)
    return (
        "<section><h2>生成ジョブ</h2>"
        '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
        f'<th title="{_escape(pages.TERM_HELP["job_state"])}">状態</th>'
        f'<th title="{_escape(pages.TERM_HELP["kind"])}">種別</th>'
        f'<th title="{_escape(pages.TERM_HELP["run"])}">run</th><th>操作</th>'
        f"</tr></thead><tbody>{rows}</tbody></table></div></section>"
    )


def _generation_job_row(job):
    actions = [f'<a href="/jobs/{_url(job["job_id"])}">処理画面</a>']
    output_id = job.get("output_id")
    if output_id:
        actions.append(f'<a href="/outputs/{_url(output_id)}">作品</a>')
    return (
        "<tr>"
        f"<td>{workbench_pages.state_badge(job['state'])}</td>"
        f"<td>{_escape(KIND_LABELS.get(job.get('kind'), job.get('kind')))}</td>"
        f'<td>{_escape(job.get("run_id"))}</td>'
        f'<td class="wb-actions">{" ".join(actions)}</td>'
        "</tr>"
    )


# --------------------------------------------------------------------------
# §3.1 -- generation confirmation page
# --------------------------------------------------------------------------

def build_confirmation(handler, run_id, query=None):
    job_store = _job_store(handler)
    repository = handler.repository
    if job_store is None or repository.catalog is None:
        handler._send_html(_guidance_page(phase="stage"))
        return
    catalog, selections = repository.catalog, repository.selections
    query = _query(handler) if query is None else query

    def qval(name):
        values = query.get(name)
        return values[0] if values else None

    kind = qval("kind")
    if kind not in ("synopsize", "narrate"):
        # run_id is the catalog run_id; the header wants the experiment
        # folder name (same distinction as everywhere else in this module).
        try:
            run_name = catalog.resolve(run_id)[0].name
        except (ConfigError, OSError, ValueError, KeyError, TypeError):
            run_name = None
        handler._send_html(pages.document(
            "生成の確認",
            '<p class="error">生成種別（kind）が不正です</p>'
            f'<p><a href="/runs/{_url(run_id)}/candidates">候補一覧に戻る</a></p>',
            phase="stage", run=run_name, output_run=run_id,
            lead="生成内容を確認して開始します。",
            job_store=job_store,
        ))
        return
    mode = qval("mode") or "missing_or_failed"
    if mode not in ("missing_or_failed", "regenerate"):
        mode = "missing_or_failed"
    from_output = qval("from_output")
    ack_requested = qval("ack") == "1"
    candidate_ids = sorted(set(query.get("candidate", [])))

    root, legacy = catalog.resolve(run_id)
    selected = selections.get(run_id)
    snapshot = catalog.snapshot(run_id, revision=selected["source_publication_revision"],
                                 manifest_sha256=selected["source_manifest_sha256"])
    by_id = {c["candidate_id"]: c for c in snapshot["candidates"]["candidates"]}
    adopted = sorted(e["candidate_id"] for e in selected["entries"] if e["state"] == "adopted")
    if kind == "narrate" and not candidate_ids:
        candidate_ids = adopted

    errors = []
    if not candidate_ids:
        errors.append("対象候補を選択してください")
    for cid in candidate_ids:
        candidate = by_id.get(cid)
        if candidate is None or not candidate.get("screenable"):
            errors.append(f"{_short_id(cid)}: 到達済みで原記録が一致する候補だけ生成できます")
        elif kind == "narrate" and cid not in adopted:
            errors.append(f"{_short_id(cid)}: 上映対象は固定選定版の採用候補だけです")

    if legacy:
        config_id = qval("config")
        config_note = "（旧実験のため後から指定した文章化用設定）"
        if not config_id:
            errors.append("旧実験のため文章化用の設定（config）を指定してください")
    else:
        manifest = read_json(contained(root, "manifest.json"))
        config_id = manifest.get("config_id")
        config_note = "（GA実行時に記録された設定。クエリのconfigは無視されます）"
        if not config_id:
            errors.append("実行設定の記録がありません")

    config = None
    if config_id:
        try:
            config = job_store.configs.get(config_id)
        except (ConfigError, FileNotFoundError):
            errors.append("指定された設定が見つかりません")
            config_id = None

    synopsis_refs = {}
    if kind == "narrate" and config is not None:
        synopsis_refs = _auto_synopsis_refs(job_store, run_id, set(candidate_ids))

    attempt_ids = []
    if ack_requested and from_output:
        try:
            prior_output = job_store.output(from_output)
        except (ConfigError, FileNotFoundError, OSError, ValueError, KeyError, TypeError):
            prior_output = None
        if prior_output:
            attempt_ids = sorted({
                e["attempt_id"] for e in prior_output.get("entries", [])
                if e.get("status") == "unknown" and e.get("candidate_id") in candidate_ids
            })
    acknowledge_unknown = bool(attempt_ids)

    generation = None
    check = None
    if config is not None:
        settings_path = getattr(handler.server, "settings_path", None)
        try:
            generation = current_generation(settings_path)
            check = generation["availability"]
        except ConfigError:
            # A broken settings.json must not 500 this page -- fall back to
            # an unavailable check so the block below still explains why,
            # with no start button (review item 2).
            generation = None
            check = {"available": False, "authentication": "unverified",
                      "reason": "settings_unreadable"}

    request_id = "req-" + uuid.uuid4().hex
    request = None
    # available=False must not offer a start button at all (the reason is
    # shown by the "現在の生成可否" block below instead).
    if config is not None and not errors and (check is None or check.get("available")):
        request = {
            "schema_version": 1, "request_id": request_id, "kind": kind, "config_id": config_id,
            "run_id": run_id, "selection_revision": selected["revision"], "candidate_ids": candidate_ids,
            "backend": generation["backend"], "model": generation["model"],
            "limits": generation["limits"], "mode": mode,
            "synopsis_refs": synopsis_refs if kind == "narrate" else {},
            "acknowledge_unknown": acknowledge_unknown, "attempt_ids": attempt_ids,
        }

    return dict(
        run_id=run_id, kind=kind, mode=mode, candidate_ids=candidate_ids, by_id=by_id,
        selection_revision=selected["revision"], config_id=config_id, config=config,
        legacy=legacy, config_note=config_note, generation=generation, check=check, request=request,
        request_id=request_id, synopsis_refs=synopsis_refs, attempt_ids=attempt_ids,
        ack_requested=ack_requested, errors=errors,
        experiment_name=snapshot["experiment_name"],
    )


def _generate_confirm(handler, run_id):
    view = build_confirmation(handler, run_id)
    if view is None:
        return
    from viewer import sifting_pages
    if view["kind"] == "narrate":
        return sifting_pages.tray(handler, run_id=run_id, confirmation=view)
    return sifting_pages.synopsis_confirm(handler, run_id, view)


def _render_generate_body(*, run_id, kind, mode, candidate_ids, by_id, selection_revision,
                           config_id, config, legacy, config_note, generation, check, request, request_id,
                           synopsis_refs, attempt_ids, ack_requested, errors):
    parts = [f'<p><a href="/runs/{_url(run_id)}/candidates">← 候補一覧に戻る</a></p>']
    parts.append(
        f'<p>種別: {_escape(KIND_LABELS.get(kind, kind))} ・ '
        f'方法: {_escape(MODE_LABELS.get(mode, mode))} ・ 選定版: {_escape(selection_revision)}</p>'
    )
    if errors:
        parts.append('<ul class="error">' + "".join(f"<li>{_escape(e)}</li>" for e in errors) + "</ul>")

    rows = []
    for cid in candidate_ids:
        c = by_id.get(cid)
        if c is None:
            rows.append(f'<tr><td>{_escape(_short_id(cid))}</td><td colspan="3">候補台帳にありません</td></tr>')
            continue
        rows.append(
            "<tr>"
            f'<td title="{_escape(cid)}">{_escape(_short_id(cid))}</td>'
            f'<td>{_escape(c.get("cell_key"))}</td>'
            f'<td>g{_escape(c.get("generation"))}/seed{_escape(c.get("seed"))}</td>'
            f'<td>{_escape(_reached_text(c.get("reached")))}</td>'
            "</tr>"
        )
    parts.append(
        '<div class="generate-summary"><div class="grid-wrap"><table class="wb-table"><thead><tr>'
        "<th>候補ID</th><th>セル</th><th>世代/seed</th><th>到達</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )

    if config_id:
        if config is not None:
            parts.append(
                f'<p>設定: <a href="/configs/{_url(config_id)}">{_escape(config.get("label") or config_id)}</a> '
                f'（{_escape(config_id)}） {_escape(config_note)}</p>'
            )
        else:
            parts.append(f'<p>設定: {_escape(config_id)} {_escape(config_note)}</p>')
    parts.append(f'<p>予定呼出し数: {_escape(len(candidate_ids))} 件（候補数）</p>')
    if generation is not None:
        limits = generation["limits"]
        parts.append(
            f'<p>文章生成: {_escape(generation["backend"])} ・ {_escape(generation.get("model") or "—")}'
            '（<a href="/configs#output">⚙ 設定で変更</a>）</p>'
            f'<p>max_calls: {_escape(limits["max_calls"])} ・ call_timeout_seconds: {_escape(limits["call_timeout_seconds"])} ・ '
            f'wall_seconds: {_escape(limits["wall_seconds"])} ・ max_saved_response_bytes: {_escape(limits["max_saved_response_bytes"])}</p>'
        )
        if generation["backend"] == "none":
            parts.append('<p class="muted">プロンプト保存のみ（本文は生成されません）</p>')
        # max_calls==0 with a real backend is still a hard cap (0 calls will be
        # made), so the truthiness check must not swallow that case.
        elif len(candidate_ids) > limits["max_calls"]:
            parts.append(
                f'<p class="warning">上限 {_escape(limits["max_calls"])} 件で残りは未開始（skipped_limit）になります</p>'
            )
    if check is not None:
        avail_text = "可" if check.get("available") else "不可"
        reason = (f' ・ 理由: {_escape(workbench_pages.availability_label(check))}'
                  if check.get("reason") and not check.get("available") else "")
        parts.append(
            f'<p>現在の生成可否: {_escape(avail_text)} ・ 認証: {_escape(check.get("authentication"))}{reason}</p>'
            '<p class="muted">認証の有効性は実呼出しまで未確認</p>'
        )
    if kind == "narrate" and config is not None:
        if synopsis_refs:
            items = "".join(
                f'<li>{_escape(_short_id(cid))}: <a href="/outputs/{_url(ref["output_id"])}#entry-{_url(cid)}">'
                f'{_escape(_short_id(ref["output_id"]))} / {_escape(_short_id(ref["attempt_id"]))}</a></li>'
                for cid, ref in sorted(synopsis_refs.items())
            )
            parts.append(f"<p>入力あらすじ（自動選択）:</p><ul>{items}</ul>")
        else:
            parts.append('<p class="muted">あらすじなしで本文化します</p>')
    parts.append("</div>")  # .generate-summary

    ack_block = ""
    if attempt_ids:
        ack_block = (
            '<p class="error">前回の呼出しは結果不明です。'
            "再生成すると同じ候補に二重に課金される可能性があります</p>"
            '<label><input type="checkbox" data-ack required> 二重生成の可能性を確認した</label>'
        )
    elif ack_requested:
        parts.append('<p class="muted">結果不明の候補は見つかりませんでした</p>')

    button = '<button type="submit">この内容で生成を開始</button>' if request is not None else ""
    request_attr = (
        f' data-request="{_escape(json.dumps(request, ensure_ascii=False, sort_keys=True))}"'
        if request is not None else ""
    )
    return (
        f'<section data-wb="generate" data-run-id="{_escape(run_id)}" '
        f'data-request-id="{_escape(request_id)}"{request_attr}>'
        + "".join(parts)
        + '<form><p class="form-error" data-form-error role="alert"></p>'
        + ack_block + button + "</form>"
        + "</section>"
    )


# --------------------------------------------------------------------------
# §3.3 -- output list
# --------------------------------------------------------------------------

def _outputs_list(handler):
    from viewer.screening_pages import render
    return render(handler)


def _reader_summary_note():
    return (
        '<p class="muted">読者向け要約（照合済み・未照合とも）は候補の詳細ページに別枠で表示され、'
        "ここに並ぶ生成稿とは別の成果物です</p>"
    )


OUTPUTS_GLOSSARY_KEYS = (
    "run", "output_id", "kind", "job_state", "counts", "selection_revision",
    "backend_model", "targets", "config_id",
)


def render_outputs_list(outputs, run_names=None, dates=None):
    run_names = run_names or {}
    if not outputs:
        return "<p>生成した作品はまだありません。候補一覧から生成できます。</p>" + pages.glossary(OUTPUTS_GLOSSARY_KEYS) + _reader_summary_note()
    by_run = {}
    broken = []
    for output in outputs:
        request = output.get("request")
        if request is None:
            broken.append(output)
            continue
        by_run.setdefault(request["run_id"], []).append(output)
    sections = []
    for run_id, items in sorted(by_run.items()):
        if dates is not None:
            items.sort(key=lambda o: dates.get(o["output_id"]) or 0, reverse=True)
        rows = "".join(_output_row(o, dates.get(o["output_id"]) if dates is not None else None) for o in items)
        heading = _escape(run_names.get(run_id, run_id))
        sections.append(
            f'<section class="card"><h2>run: {heading} '
            f'<a href="/runs/{_url(run_id)}/candidates">候補一覧</a></h2>'
            '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
            "<th>output_id</th><th>種別</th><th>状態</th><th>内訳</th><th>操作</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div></section>"
        )
    if broken:
        # execution/jobs.py's outputs() (eeea3eb) reports one damaged output as
        # {"output_id", "error": {"code", "message"}} instead of failing the
        # whole listing; render it as a row needing attention, not a crash.
        items = "".join(
            f'<li>{_escape(o.get("output_id"))}: 破損・要確認（{_escape((o.get("error") or {}).get("code"))}）'
            f' {_escape((o.get("error") or {}).get("message", ""))}</li>'
            for o in broken
        )
        sections.append(f'<section class="card"><h2>読み取れない生成版</h2><ul>{items}</ul></section>')
    return "".join(sections) + pages.glossary(OUTPUTS_GLOSSARY_KEYS) + _reader_summary_note()


def _output_row(output, created=None):
    request = output["request"]
    output_id = output["output_id"]
    job_state = output.get("job_state")
    entries = output.get("entries") or []
    total = len(request.get("candidate_ids") or entries)
    if job_state and job_state not in TERMINAL:
        completed = sum(1 for e in entries if e.get("status") not in ("pending", "running"))
        state_text = f"生成中（{completed}/{total}）"
    else:
        completion_kind = output.get("completion_kind")
        state_text = COMPLETION_LABELS.get(completion_kind, completion_kind or "—")
    counts = output.get("counts") or {}
    counts_text = " ・ ".join(
        f"{ENTRY_STATUS_LABELS.get(status, status)} {n}" for status, n in sorted(counts.items())
    ) or "—"
    config_id = request.get("config_id")
    # WB-UI-014 §3.2: 選定版/backend-model/対象数/設定 move into a collapsed
    # detail row; the list keeps only what Sifting scans at a glance.
    detail_id = f"detail-{_escape(output_id)}"
    date_html = ""
    if created is not None:
        from viewer.screening_view import date_label
        date_html = f'<strong>{_escape(date_label(created))}</strong><br>'
    row = (
        "<tr>"
        f'<td>{date_html}<a href="/outputs/{_url(output_id)}">{_escape(_short_id(output_id))}</a></td>'
        f'<td>{_escape(KIND_LABELS.get(request.get("kind"), request.get("kind")))}</td>'
        f'<td>{_escape(state_text)}</td>'
        f'<td>{_escape(counts_text)}</td>'
        '<td class="wb-actions">'
        f'<a href="/outputs/{_url(output_id)}">開く</a> '
        f'<button type="button" class="row-toggle" aria-expanded="false" aria-controls="{detail_id}">詳細</button>'
        "</td></tr>"
    )
    detail = (
        f'<tr id="{detail_id}" class="detail-row" hidden><td colspan="5">'
        '<dl class="metric">'
        f'<dt>{pages.term("selection_revision", "選定版")}</dt><dd>{_escape(request.get("selection_revision"))}</dd>'
        f'<dt>{pages.term("backend_model", "backend/model")}</dt>'
        f'<dd>{_escape(request.get("backend"))}/{_escape(request.get("model") or "—")}</dd>'
        f'<dt>{pages.term("targets", "対象数")}</dt><dd>{_escape(total)}</dd>'
        f'<dt>{pages.term("config_id", "設定")}</dt>'
        f'<dd><a href="/configs/{_url(config_id)}">{_escape(config_id)}</a></dd>'
        "</dl></td></tr>"
    )
    return row + detail


# --------------------------------------------------------------------------
# §3.4 -- output detail
# --------------------------------------------------------------------------

def _output_detail(handler, output_id):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="stage"))
        return
    try:
        output = job_store.output(output_id)
    except ConfigError as error:
        raise ConfigError("output_id", "作品が見つからないか読み取れません", code=error.code) from error
    except FileNotFoundError as error:
        raise ConfigError("output_id", "作品が見つからないか読み取れません", code="not_found") from error
    # Any other storage failure (OSError/ValueError/KeyError/TypeError) is left
    # uncaught here and falls through to dispatch()'s own handler, which turns
    # it into a 500 with code="storage_error".

    request = output["request"]
    if request["kind"] == "narrate" and _query(handler).get("view") != ["record"]:
        from viewer.screening_pages import render
        return render(handler, output_id)
    job_state = output.get("job_state")
    # A job_state-less output has no owning job record at all; treat it like a
    # terminal output (nothing to poll, nothing to reconcile) with only the
    # "別の稿を作る" recourse offered.
    terminal = job_state is None or job_state in TERMINAL
    run_id = request["run_id"]
    kind = request["kind"]
    entries = output.get("entries") or []
    synopsis_refs = request.get("synopsis_refs") or {}

    try:
        candidates_by_id = {c["candidate_id"]: c for c in handler.repository.catalog.candidates(run_id)["candidates"]}
    except (ConfigError, FileNotFoundError, OSError, ValueError, KeyError, TypeError):
        candidates_by_id = {}

    try:
        history_record = next((r for r in handler.repository.catalog.history() if r["run_id"] == run_id), None)
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        history_record = None
    experiment_name = history_record.get("experiment_name") if history_record else None

    store = OutputStore(job_store.configs.control)

    kind_line = f'<p>種別: {_escape(KIND_LABELS.get(kind, kind))}</p>'
    if job_state is None:
        job_line = '<p class="warning">所有ジョブの記録がありません</p>'
    else:
        job_line = (
            f'<p>ジョブ: <a href="/jobs/{_url(request.get("job_id"))}">処理画面</a> {workbench_pages.state_badge(job_state)}</p>'
        )
    run_line = f'<p>run: <a href="/runs/{_url(run_id)}/candidates">候補一覧</a>'
    if experiment_name:
        run_line += f' <a href="/exp/{_url(experiment_name)}">格子</a>'
    run_line += "</p>"
    header = [
        kind_line, job_line, run_line,
        f'<p>選定版: {_escape(request.get("selection_revision"))} ・ 公開版: {_escape(request.get("publication_revision"))}</p>',
    ]
    if config_id := request.get("config_id"):
        note = " （後から指定した文章化用設定）" if request.get("settings_provenance") == "posthoc_generation_config" else ""
        header.append(f'<p>設定: <a href="/configs/{_url(config_id)}">実行設定</a> （{_escape(config_id)}）{note}</p>')
    limits = request.get("limits") or {}
    header.append(
        f'<p>backend: {_escape(request.get("backend"))} ・ model: {_escape(request.get("model") or "—")} ・ '
        f'方法: {_escape(MODE_LABELS.get(request.get("mode"), request.get("mode")))}</p>'
    )
    if limits:
        header.append(
            f'<p>max_calls: {_escape(limits.get("max_calls"))} ・ '
            f'call_timeout_seconds: {_escape(limits.get("call_timeout_seconds"))} ・ '
            f'wall_seconds: {_escape(limits.get("wall_seconds"))} ・ '
            f'max_saved_response_bytes: {_escape(limits.get("max_saved_response_bytes"))}</p>'
        )
    header.append(
        f'<p>input_manifest_sha256: {_escape(_short_id(request.get("input_manifest_sha256")))} ・ '
        f'config_sha256: {_escape(_short_id(request.get("config_sha256")))}</p>'
    )
    if terminal and output.get("completion_kind"):
        header.append(f'<p>{_escape(COMPLETION_LABELS.get(output["completion_kind"], output["completion_kind"]))}</p>')
    if not terminal:
        header.append("<p>生成中です。しばらくすると自動的に更新されます。</p>")
    header.append('<p class="warning" data-connection-status hidden>サーバーに接続できません（再試行中）</p>')

    regenerate_targets = sorted(request.get("candidate_ids") or [])
    ops = []
    if terminal and job_state is None:
        if regenerate_targets:
            href = f"/runs/{_url(run_id)}/generate?kind={kind}&mode=regenerate&from_output={_url(output_id)}"
            for c in regenerate_targets:
                href += f"&candidate={_url(c)}"
            ops.append(f'<a href="{_escape(href)}">別の稿を作る</a>')
    elif terminal:
        retry_targets = sorted(
            e["candidate_id"] for e in entries
            if (e.get("status") == "error" and e.get("retry_policy") == "safe_new_request")
            or e.get("status") in _RETRY_TARGET_STATUSES
        )
        unknown_targets = sorted(e["candidate_id"] for e in entries if e.get("status") == "unknown")
        recoverable = any(e.get("retry_policy") == "local_recovery_only" for e in entries)

        def qs(candidates):
            return "&".join(f"candidate={_url(c)}" for c in candidates)

        if retry_targets:
            href = (
                f"/runs/{_url(run_id)}/generate?kind={kind}&mode=missing_or_failed"
                f"&from_output={_url(output_id)}&{qs(retry_targets)}"
            )
            ops.append(f'<a href="{_escape(href)}">未生成・失敗分を生成</a>')
        if regenerate_targets:
            href = (
                f"/runs/{_url(run_id)}/generate?kind={kind}&mode=regenerate"
                f"&from_output={_url(output_id)}&{qs(regenerate_targets)}"
            )
            ops.append(f'<a href="{_escape(href)}">別の稿を作る</a>')
        if unknown_targets:
            href = (
                f"/runs/{_url(run_id)}/generate?kind={kind}&mode=regenerate&ack=1"
                f"&from_output={_url(output_id)}&{qs(unknown_targets)}"
            )
            ops.append(f'<a href="{_escape(href)}">結果不明の候補を確認して再生成</a>')
        if recoverable:
            ops.append('<button type="button" data-action="recover">復旧</button><span data-recover-status></span>')

    entry_sections = "".join(
        _entry_article(entry, candidates_by_id, store, output_id, run_id, synopsis_refs.get(entry["candidate_id"]))
        for entry in entries
    )

    siblings = [
        o for o in _run_outputs(job_store, run_id, kind=kind) if o["output_id"] != output_id
    ]
    sibling_html = ""
    if siblings:
        rows = "".join(
            f'<tr><td><a href="/outputs/{_url(o["output_id"])}">{_escape(_short_id(o["output_id"]))}</a></td>'
            f'<td>{_escape(COMPLETION_LABELS.get(o.get("completion_kind"), o.get("completion_kind") or "—"))}</td></tr>'
            for o in siblings
        )
        sibling_html = (
            '<section class="card"><h2>同じrunの他の稿</h2>'
            '<div class="grid-wrap"><table class="wb-table"><thead><tr><th>output_id</th><th>状態</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div></section>"
        )

    ops_html = ""
    if terminal:
        if ops:
            ops_html = f'<p class="actions">{"".join(ops)}</p>'
    else:
        ops_html = '<p class="muted">生成中は操作できません</p>'

    body = (
        f'<section data-wb="output" data-output-id="{_escape(output_id)}" '
        f'data-job-id="{_escape(request.get("job_id") or "")}" '
        f'data-terminal="{"true" if terminal else "false"}" '
        f'data-terminal-states="{_escape(_TERMINAL_JSON)}" '
        f'data-status-labels="{_escape(_STATUS_LABELS_JSON)}" '
        f'data-entry-labels="{_escape(_STATUS_LABELS_JSON)}">'
        + "".join(header) + ops_html + entry_sections
        + "</section>" + sibling_html
    )
    from viewer.screening_pages import shell, link
    return_link = f"/outputs/{_url(output_id)}" if kind == "narrate" else f"/outputs?run={_url(run_id)}&view=history"
    try:
        history = handler.repository.catalog.history()
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        history = []
    shell(handler, "生成記録", '<div class="sc-history"><p>' + link(return_link, "← 作品・履歴へ戻る") + '</p>' + body + '</div>',
          query={"run": [run_id]}, history=history, active="history")



def _entry_article(entry, candidates_by_id, store, output_id, run_id, synopsis_ref=None):
    cid = entry["candidate_id"]
    status = entry.get("status")
    candidate = candidates_by_id.get(cid)
    label = _escape(_short_id(cid))
    if candidate:
        label += (
            f' ・ {_escape(candidate.get("cell_key"))} ・ '
            f'g{_escape(candidate.get("generation"))}/seed{_escape(candidate.get("seed"))}'
        )
    parts = [
        f'<article id="entry-{_escape(cid)}" data-entry="{_escape(cid)}" data-status="{_escape(status)}">',
        f"<h3>{label} {workbench_pages.state_badge(status, labels=ENTRY_STATUS_LABELS)}</h3>",
        f'<p><a href="/runs/{_url(run_id)}/candidates/{_url(cid)}/raw">候補の原ログ</a></p>',
        f'<p data-field="message">{_escape(entry.get("message") or "")}</p>',
    ]
    if synopsis_ref:
        parts.append(
            f'<p>入力あらすじ: {_escape(synopsis_ref["output_id"])} / {_escape(synopsis_ref["attempt_id"])} '
            f'<a href="/outputs/{_url(synopsis_ref["output_id"])}#entry-{_url(cid)}">あらすじ稿を見る</a></p>'
        )
    retry_text = RETRY_LABELS.get(entry.get("retry_policy"), "")
    if retry_text:
        parts.append(f'<p class="muted">{_escape(retry_text)}</p>')
    parts.append(
        "<details><summary>詳細</summary>"
        f'<p>code: {_escape(entry.get("code"))} ・ stage: {_escape(entry.get("stage"))} ・ '
        f'cause_type: {_escape(entry.get("cause_type"))}</p></details>'
    )
    if status == "ok" and entry.get("text_ref"):
        try:
            raw = verified(contained(store.folder(output_id), entry["text_ref"]), entry["text_sha256"])
            text = raw.decode("utf-8")
        except (ConfigError, OSError, UnicodeDecodeError):
            text = None
        if text is None:
            parts.append('<p class="error">本文を読み込めません</p>')
        else:
            paragraphs = "".join(f"<p>{_escape(p)}</p>" for p in text.split("\n\n") if p.strip())
            parts.append(f'<div class="story-text" data-field="text">{paragraphs}</div>')
    elif status == "prompt_only":
        parts.append("<p>本文は未生成です</p>")
        try:
            prompt = _prompt_text(store, output_id, cid)
            parts.append(f"<details><summary>プロンプト</summary><pre>{_escape(prompt)}</pre></details>")
        except (ConfigError, OSError):
            pass
    if entry.get("usage") is None:
        parts.append('<p class="muted">トークン実測: 不明</p>')
    parts.append("</article>")
    return "".join(parts)


# --------------------------------------------------------------------------
# §2 --稿本文・プロンプトの読取エンドポイント
# --------------------------------------------------------------------------

def _entry_text(handler, output_id, candidate_id):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page())
        return
    output = job_store.output(output_id)
    entry = next((e for e in output.get("entries", []) if e["candidate_id"] == candidate_id), None)
    if entry is None or entry.get("status") != "ok" or not entry.get("text_ref"):
        raise ConfigError("candidate_id", "本文がありません", code="not_found")
    store = OutputStore(job_store.configs.control)
    raw = verified(contained(store.folder(output_id), entry["text_ref"]), entry["text_sha256"])
    handler._send_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", raw)


def _entry_prompt(handler, output_id, candidate_id):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page())
        return
    store = OutputStore(job_store.configs.control)
    prompt = _prompt_text(store, output_id, candidate_id)
    handler._send_bytes(HTTPStatus.OK, "text/plain; charset=utf-8", prompt.encode("utf-8"))


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _resolve(parts):
    if not parts:
        return None
    if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "generation-plan":
        from viewer.generation_pages import api_plan
        return api_plan, (parts[2],)
    if parts[0] == "runs" and len(parts) == 3 and parts[2] == "generate":
        return _generate_confirm, (parts[1],)
    if parts[0] != "outputs":
        return None
    if len(parts) == 1:
        return _outputs_list, ()
    if len(parts) == 2:
        return _output_detail, (parts[1],)
    if len(parts) == 5 and parts[2] == "entries" and parts[4] == "text":
        return _entry_text, (parts[1], parts[3])
    if len(parts) == 5 and parts[2] == "entries" and parts[4] == "prompt":
        return _entry_prompt, (parts[1], parts[3])
    return None


def dispatch(handler, parts, method):
    if method != "GET":
        return False
    route = _resolve(parts)
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
        job_api.send_error(handler, ConfigError("resource", "作品が見つからないか読み取れません", code="not_found"))
    except (data.BadRequest, data.ForbiddenPath, data.MissingResource):
        raise
    except (OSError, ValueError, TypeError, KeyError):
        if method == "GET" and parts[0] != "api":
            raise OSError("保存された情報を読み取れません")
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
            "code": "storage_error", "message": "保存済み記録を処理できません",
            "field_errors": {}, "retryable": False, "current_revision": None,
        })
    return True
