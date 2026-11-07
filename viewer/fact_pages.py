"""Fact-preserving scene workspace, frozen prompt previews and versioned results."""
from http import HTTPStatus
import json
from urllib.parse import parse_qs, urlsplit
from execution.provenance import ConfigError, contained, read_json
from execution.fact_workspace import FactWorkspace
from execution.fact_service import pinned_package, process_fact_output
from execution.output_store import OutputStore, verified
from gapengine.fact_contract import COMMON, DEFAULTS, build_prompt, packet
from viewer import pages, job_api, workbench_pages as wb

E, U = pages._escape, pages._url_segment
KINDS = {"world_fact": "世界の事実", "claim": "話者の主張", "delivery": "通知の到達", "notice": "気づき",
         "understanding": "理解", "belief": "人物の認識", "situation": "状況", "agreement": "合意",
         "action_candidate": "行動候補", "action_selected": "選択", "action_execution": "実行", "action_result": "結果"}


def context(handler):
    jobs = wb._job_store(handler)
    if jobs is None:
        raise ConfigError("facts", "Studioで場面の文章化を開いてください", code="unavailable")
    return jobs, FactWorkspace(jobs.configs, handler.repository.catalog)


def document(handler, title, body, initial=None):
    doc = pages.document(title, '<section class="fact-workspace" data-facts="' + E(json.dumps(initial or {}, ensure_ascii=False)) + '">' + body + '</section>',
                         phase="sifting", job_store=wb._job_store(handler), page_class="form-workspace", stylesheets=('fact-workspace.css',), scripts=('fact-workspace.js',))
    handler._send_html(doc)


def config_id(handler, rid, query):
    root, legacy = handler.repository.catalog.resolve(rid)
    return query.get("config", [None])[0] if legacy else read_json(contained(root, "manifest.json"))["config_id"]


def workspace(handler, rid, cid):
    jobs, store = context(handler)
    query = parse_qs(urlsplit(handler.path).query)
    revision = query.get("revision", [None])[0]
    if revision is not None:
        if not revision.isdecimal():
            raise ConfigError("revision", "保存履歴の整数の版を指定してください")
        revision = int(revision)
    cfg = config_id(handler, rid, query)
    current = store.current(rid, cid)
    if current is not None:
        cfg = current["config_id"]
    if not cfg:
        options = ''.join('<option value="' + E(c["config_id"]) + '">' + E(c["label"]) + '</option>' for c in jobs.configs.list())
        body = '<h1>場面の文章化</h1><p>この記録と照合する固定設定を選んでください。</p><form method="get"><label>文章化に使う設定<select name="config" required>' + options + '</select></label><button>設定を選んで開く</button></form>'
        return document(handler, "場面の文章化", body)
    ctx = store.context(rid, cid, cfg, revision)
    saved = ctx["saved"]; readonly = revision is not None
    initial = {"run_id": rid, "candidate_id": cid, "config_id": cfg, "revision": saved["revision"] if saved else 0,
               "package_sha256": saved["reference"]["package_sha256"] if saved else None,
               "readonly": readonly, "source_changed": ctx["source_changed"], "defaults": ctx["defaults"]}
    disabled = ' disabled' if readonly else ''
    body = '<header class="fact-heading"><div><p class="fact-eyebrow">WorldBloom · 場面の文章化</p><h1>記録から、あなたの文体へ</h1><p>書く内容を選び、あらすじと本文の書き方を調整します。</p></div><a href="/stories/' + U(rid) + '/' + U(cid) + '?config=' + U(cfg) + '">素材・骨格へ</a></header>'
    label = '保存済み ' + str(saved["revision"]) + '版' if saved else '未保存の下書き'
    body += '<div class="fact-state"><strong data-fact-state>' + E(label) + (' · 閲覧のみ' if readonly else '') + '</strong><span>生成には保存済みの版を使います</span></div><p role="status" data-fact-message></p>'
    if ctx["source_changed"]:
        body += '<p class="fact-alert">元の記録または固定設定が変わっています。保存済み版は閲覧できます。新しく生成する前に入力を確認して保存してください。</p>'
    if readonly:
        body += '<p><a href="/facts/' + U(rid) + '/' + U(cid) + '?config=' + U(cfg) + '">現在の編集画面へ戻る</a></p>'
        p = saved["package"]
        initial["saved_preview"] = {"inputs": {k: packet(p, k) for k in DEFAULTS}, "prompts": {k: build_prompt(p, k) for k in DEFAULTS}, "conflicts": {k: [] for k in DEFAULTS}}
    records = ctx["supplement"]["records"]
    if not records:
        body += '<section class="fact-card"><h2>このログには新しい型付き記録がありません</h2><p>旧ログから、気づきや理解を補ったものとして扱いません。素材・骨格の既存経路をご利用ください。</p></section>'
        return document(handler, "場面の文章化", body, initial)
    selection = ctx["selection"]
    body += '<div class="fact-columns"><section class="fact-card"><h2>1. 書く内容</h2><label>視点<input data-fact-viewpoint value="' + E(selection["viewpoint"]) + '"' + disabled + '></label><p>必要な前提は一緒に残します。非開示の記録は生成入力へ渡しません。</p><details><summary>記録の採用・必須・非開示を選ぶ · ' + str(len(records)) + '件</summary><div class="fact-records">'
    for r in records:
        flags = ''.join('<label><input type="checkbox" data-fact-flag="' + flag + '" data-record="' + E(r["id"]) + '"' + (' checked' if r["id"] in selection[flag] else '') + disabled + '>' + label + '</label>' for flag, label in (("selected", "採用"), ("required", "必須"), ("hidden", "非開示")))
        body += '<article class="fact-record"><strong>' + E(KINDS[r["kind"]]) + ' · ' + E(r["actor"]) + '</strong><small>時点 ' + str(r["at"]) + ' · ' + E(r["id"]) + '</small><p>' + E(json.dumps(r["content"], ensure_ascii=False)) + '</p><div class="fact-flags">' + flags + '</div></article>'
    body += '</div></details><label>本文へ引き継ぐあらすじの生成ID（任意）<input data-fact-synopsis value="' + E((saved["package"].get("synopsis") or {}).get("output_id", "") if saved else "") + '"' + disabled + '></label><p class="fact-note">同じ原記録・選択・開示範囲のあらすじだけを使います。必要な固定事実も本文へ渡します。</p></section>'
    body += '<section class="fact-card"><h2>2. 書き方</h2><label>あらすじの書き方<textarea rows="5" data-fact-style="synopsize"' + disabled + '>' + E(ctx["styles"]["synopsize"]) + '</textarea></label><label>本文の書き方<textarea rows="7" data-fact-style="narrate"' + disabled + '>' + E(ctx["styles"]["narrate"]) + '</textarea></label>'
    body += '<button type="button" class="fact-secondary" data-fact-action="reset"' + disabled + '>書き方を既定値に戻す</button><p class="fact-note">変更は下書きに反映します。過去の版や生成結果は保持します。</p><details><summary>共通の事実保持条件 · ' + E(ctx["common_version"]) + '</summary><pre>' + E(COMMON) + '</pre></details><p class="fact-note">文体の指示は、人物の知識や事実保持条件を上書きしません。自由な指示の矛盾を自動ですべて判断できるわけではありません。</p></section></div>'
    body += '<section class="fact-card"><h2>3. 次回の入力を確かめる</h2><div class="fact-actions"><button type="button" data-fact-action="preview" data-kind="synopsize">あらすじ用をプレビュー</button><button type="button" data-fact-action="preview" data-kind="narrate">本文用をプレビュー</button></div><p data-fact-conflicts role="alert"></p><div data-fact-preview hidden><h3 data-fact-preview-title></h3><details open><summary>選択した可視入力</summary><pre data-fact-input></pre></details><details><summary>次回生成に使う実効プロンプト</summary><pre data-fact-prompt></pre></details><p>このプレビューは生成を開始しません。</p></div></section>'
    generation = ' disabled' if readonly or not saved or ctx["source_changed"] else ''
    body += '<section class="fact-card fact-save"><div><h2>保存して生成</h2><p>保存前の編集は、次回の生成に使いません。</p></div><div class="fact-actions"><button type="button" data-fact-action="save"' + disabled + '>新しい版として保存</button><button type="button" data-fact-action="generate" data-kind="synopsize"' + generation + '>保存済み版であらすじを作る</button><button type="button" data-fact-action="generate" data-kind="narrate"' + generation + '>保存済み版で本文を書く</button></div></section>'
    history = store.history(rid, cid)
    body += '<section class="fact-card"><h2>保存履歴</h2>' + ('<ol>' + ''.join('<li><a href="/facts/' + U(rid) + '/' + U(cid) + '?config=' + U(cfg) + '&revision=' + str(h["revision"]) + '">' + str(h["revision"]) + '版を読む</a></li>' for h in history) + '</ol>' if history else '<p>最初の保存で履歴が作られます。</p>') + '</section>'
    document(handler, "場面の文章化", body, initial)


def output(handler, oid, cid):
    jobs, _ = context(handler)
    store = OutputStore(jobs.configs.control)
    request = store.request(oid)
    if request.get("pipeline") != "facts_v1" or cid not in request["candidate_ids"]:
        raise ConfigError("output", "場面の文章化の生成結果ではありません", code="not_found")
    store.verify_artifacts(oid)
    package = pinned_package(store.folder(oid), cid, request)
    result = process_fact_output(store, oid, cid)
    body = '<header class="fact-heading"><div><p class="fact-eyebrow">WorldBloom · 保存された生成結果</p><h1>' + ('あらすじ' if request["kind"] == "synopsize" else '場面の本文') + '</h1><p>この結果に実際に使った入力と書き方を表示しています。</p></div><a href="/facts/' + U(request["run_id"]) + '/' + U(cid) + '?config=' + U(request["config_id"]) + '">次の版を編集する</a></header>'
    body += '<section class="fact-card"><h2>文章</h2><div class="fact-text">' + E((result or {}).get("text") or "本文はまだありません。生成状況と保存済みプロンプトを確認してください。") + '</div></section>'
    if result:
        v = result["validation"]
        body += '<section class="fact-card"><h2>確認状況</h2><p>形式・参照：' + E(v["structural_status"]) + ' ／ 型付き対応記録：' + E(v["typed_meaning_status"]) + '</p><p class="fact-alert">文章の意味：未検証 ／ 利用者の採否：未決定</p><p>参照や対応記録の一致だけで、文章の意味が正しいと判断しません。</p><details><summary>検査記録の詳細</summary><pre>' + E(json.dumps(v, ensure_ascii=False, indent=2)) + '</pre></details></section>'
    prompt = verified(store.sink(oid, cid).folder / "prompt.txt", store.sink(oid, cid).prompt_sha256).decode("utf-8")
    body += '<section class="fact-card"><h2>この結果の書き方</h2><p>共通条件 ' + E(package["common_version"]) + ' · 表現設定の版 ' + E(package["style_version"][:16]) + '</p><pre data-fact-used-style>' + E(package["styles"][request["kind"]]) + '</pre><details><summary>送信した実効プロンプト全文</summary><pre data-fact-used-prompt>' + E(prompt) + '</pre></details><details><summary>使った可視入力</summary><pre>' + E(json.dumps(packet(package, request["kind"]), ensure_ascii=False, indent=2)) + '</pre></details></section>'
    document(handler, "保存された文章と入力", body)


def api(handler, parts, method):
    jobs, store = context(handler)
    job_api.boundary(handler, client_header=method == "POST", body_required=method == "POST")
    if len(parts) not in (5, 6) or parts[2] != "runs":
        raise ConfigError("route", "APIがありません", code="not_found")
    rid, cid = parts[3:5]
    if len(parts) == 5 and method == "GET":
        q = parse_qs(urlsplit(handler.path).query)
        result = store.context(rid, cid, config_id(handler, rid, q))
    elif len(parts) == 6 and method == "POST":
        body = handler._request_json(); action = parts[5]
        if action == "preview":
            result = store.preview(rid, cid, body)
        elif action == "save":
            if not isinstance(body, dict) or "expected_revision" not in body:
                raise ConfigError("revision", "保存済み版を指定してください")
            revision = body.pop("expected_revision")
            result = store.save(rid, cid, body, revision)
        elif action == "generate":
            result = store.generate(jobs, rid, cid, body, getattr(handler.server, "settings_path", None))
        else:
            raise ConfigError("route", "APIがありません", code="not_found")
    else:
        raise ConfigError("route", "APIがありません", code="not_found")
    handler._send_json(HTTPStatus.OK, result)


def dispatch(handler, parts, method):
    if not ((parts and parts[0] == "facts") or parts[:2] == ["api", "facts"]):
        return False
    try:
        if parts[:2] == ["api", "facts"]:
            api(handler, parts, method)
        elif method == "GET" and len(parts) == 3:
            workspace(handler, parts[1], parts[2])
        elif method == "GET" and len(parts) == 4 and parts[1] == "outputs":
            output(handler, parts[2], parts[3])
        else:
            raise ConfigError("route", "画面がありません", code="not_found")
    except ConfigError as error:
        job_api.send_error(handler, error)
    except FileNotFoundError:
        job_api.send_error(handler, ConfigError("facts", "保存済みの記録がありません", code="not_found"))
    except (OSError, ValueError, TypeError, KeyError):
        job_api.send_error(handler, ConfigError("facts", "記録の保存状態を照合できません", code="storage_error"))
    return True
