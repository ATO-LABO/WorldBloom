"""Shared, read-only generation plans and progress surfaces."""
import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit
from execution.provenance import ConfigError
from execution.output_requests import eligible
from execution.output_store import OutputStore
from viewer import pages, workbench_pages as wb

ASSETS = '<link rel="stylesheet" href="/static/generation.css"><script src="/static/generation.js" defer></script>'
E = pages._escape


def plan(handler, run_id, query=None):
    from viewer.output_pages import build_confirmation
    query = parse_qs(urlsplit(handler.path).query) if query is None else query
    if query.get("kind", [None])[0] not in ("synopsize", "narrate"):
        raise ConfigError("kind", "生成種別が不正です", code="bad_request")
    jobs = wb._job_store(handler)
    if jobs is None or handler.repository.catalog is None:
        raise ConfigError("run_id", "生成に必要な実行記録がありません", code="unavailable")
    view = build_confirmation(handler, run_id, query)
    errors, request = list(view["errors"]), view["request"]
    active = next((j for j in jobs.list() if j.get("run_id") == run_id and j.get("state") in wb.RUNNING_STATES), None)
    accepted = []
    if request:
        try:
            accepted = eligible(OutputStore(jobs.configs.control), request)
        except (ConfigError, OSError, ValueError):
            errors.append("生成可能な対象を確認できません。生成記録・復旧状態を確認してください。")
    generation = view.get("generation") or {}
    if view.get("check") and not view["check"].get("available"):
        errors.append(wb.availability_label(view["check"]))
    if generation.get("backend") != "none" and generation.get("limits", {}).get("max_calls") == 0:
        errors.append("呼出し上限が0件です。生成設定を変更してください。")
    if active or errors or not accepted:
        request = None
    else:
        request = {**request, "candidate_ids": accepted,
                   "synopsis_refs": {k:v for k,v in request["synopsis_refs"].items() if k in accepted}}
    return {"run_id": run_id, "kind": view["kind"], "request": request, "errors": errors,
            "active_job": {k:active.get(k) for k in ("job_id", "kind")} if active else None,
            "config_id": view["config_id"], "legacy": view["legacy"],
            "configs": [{"config_id":c["config_id"], "label":c["label"]} for c in jobs.configs.list()] if view["legacy"] else [],
            "backend": generation.get("backend"), "model": generation.get("model"), "limits": generation.get("limits", {}),
            "candidates": [{"candidate_id":cid, "label":view["by_id"].get(cid, {}).get("title") or wb._short_id(cid), "eligible":cid in accepted} for cid in view["candidate_ids"]]}


def api_plan(handler, run_id):
    handler._send_json(HTTPStatus.OK, plan(handler, run_id))


def surface(initial=None, *, modal=False):
    tag = "dialog" if modal else "section"
    attr = ' aria-labelledby="gen-title"' if modal else ''
    return f'''<{tag} class="gen-surface" data-generation data-initial="{E(json.dumps(initial or {}, ensure_ascii=False))}"{attr}>
<header class="gen-heading"><div><span class="gen-eyebrow">文章の準備</span><h2 id="gen-title" data-gen-title>生成内容を確認</h2></div><button type="button" data-gen-close aria-label="閉じる">×</button></header>
<div class="gen-scroll"><p data-gen-message role="status" aria-live="polite"></p><div data-gen-settings></div>
<div class="gen-progress" data-gen-progress hidden><strong data-gen-count></strong><progress aria-label="処理済みの候補"></progress><p data-gen-phase></p></div>
<ul class="gen-entries" data-gen-entries aria-label="生成対象と結果"></ul>
<label data-gen-ack-wrap hidden><input type="checkbox" data-gen-ack>結果不明の試行を再生成します。二重生成の可能性を確認しました。</label>
<p class="gen-error" role="alert" data-gen-error></p><details data-gen-details hidden><summary>生成記録の詳細</summary><p data-gen-id></p><a data-gen-record>結果と復旧方法を確認 ↗</a></details></div>
<footer class="gen-footer"><p data-gen-note>開始するまで文章生成は行われません。</p><div><button type="button" data-gen-stop hidden>生成を停止…</button><button type="button" data-gen-secondary>戻る</button><button type="button" class="gen-primary" data-gen-primary>生成を開始</button><a class="gen-primary" data-gen-result hidden>結果を確認</a></div></footer></{tag}>'''


def render_job(handler, job):
    rid = job.get("run_id")
    title = "あらすじの生成" if job["kind"] == "synopsize" else "本文の生成"
    from viewer.output_pages import COMPLETION_LABELS
    fallback = '<noscript><p>' + E(COMPLETION_LABELS.get(job.get("completion_kind"), job.get("state"))) + '</p>'
    if job.get("output_id"):
        fallback += f'<a href="/outputs/{pages._url_segment(job["output_id"])}">生成結果を確認</a>'
    fallback += '</noscript>'
    body = '<main class="gen-page">' + surface({"job":job}) + fallback + '</main>'
    doc = pages.document(title, body, phase="sifting" if job["kind"] == "synopsize" else "stage",
                         run=wb._resolve_run_name(handler, rid), output_run=rid,
                         job_store=wb._job_store(handler), page_class="run-observer")
    handler._send_html(doc.replace('</head>', '<link rel="stylesheet" href="/static/run-workspace.css">' + ASSETS + '</head>'))
