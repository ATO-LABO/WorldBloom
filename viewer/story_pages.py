"""Story workspace: read-only facts, editable plan, pinned generation and review."""
from http import HTTPStatus
import json
from urllib.parse import parse_qs, urlsplit
from execution.provenance import ConfigError, canonical, contained, read_json, sha256
from execution.story_store import StoryStore
from execution.output_store import OutputStore, verified
from execution.story_service import (organize_studio, read_story_output, process_story_output)
from viewer import pages, job_api, workbench_pages as wb

E,U=pages._escape,pages._url_segment

def _context(handler):
    jobs=wb._job_store(handler)
    if jobs is None:
        raise ConfigError("stories","Studioで素材・骨格を整理できます",code="unavailable")
    return jobs,StoryStore(jobs.configs.control)

def _doc(handler,title,body,initial=None):
    body=body.replace("<h1>","<h2>").replace("</h1>","</h2>")
    doc=pages.document(title,'<section class="story-workspace">'+body+'</section>',
        phase="sifting",job_store=wb._job_store(handler))
    data=E(json.dumps(initial or {},ensure_ascii=False))
    doc=doc.replace('<section class="story-workspace">','<section class="story-workspace" data-story="'+data+'">')
    handler._send_html(doc.replace("</head>",'<link rel="stylesheet" href="/static/story-workspace.css"><script src="/static/story-workspace.js" defer></script></head>'))

def _config(handler,rid,query):
    root,legacy=handler.repository.catalog.resolve(rid)
    if not legacy:
        return read_json(contained(root,"manifest.json")).get("config_id")
    return query.get("config",[None])[0]

def _api(handler,parts,method):
    jobs,store=_context(handler)
    if method=="POST":
        job_api.boundary(handler)
        body=handler._request_json()
    else:
        job_api.boundary(handler,client_header=False,body_required=False)
        body={}
    if parts==["api","stories","runs"] and method=="GET":
        rid=parse_qs(urlsplit(handler.path).query).get("run",[None])[0]
        handler._send_json(HTTPStatus.OK,{"stories":store.list(rid)})
        return
    if len(parts)==5 and method=="GET":
        rid,cid=parts[3:5]
        current=store.current(rid,cid)
        if current is None:raise ConfigError("plan","素材はまだありません",code="not_found")
        material=store.material(current["plan"]["material_ref"]["material_id"])
        handler._send_json(HTTPStatus.OK,{**current,"material":material})
        return
    if len(parts)!=6 or method!="POST":
        raise ConfigError("route","APIがありません",code="not_found")
    rid,cid,action=parts[3:6]
    if action=="organize":
        if set(body)!={"config_id"}:raise ConfigError("request","設定版だけを指定してください")
        result=organize_studio(jobs.configs,handler.repository.catalog,rid,cid,body["config_id"])
    elif action=="edit":
        if set(body)!={"expected_revision","changes"}:raise ConfigError("request","版と編集項目が必要です")
        result=store.edit(rid,cid,body["changes"],expected_revision=body["expected_revision"])
    elif action=="confirm":
        if set(body)!={"plan_sha256"}:raise ConfigError("request","確認する構成案のSHAが必要です")
        current=store.current(rid,cid)
        if not current:raise ConfigError("plan","素材はまだありません")
        result=store.confirm(current["plan"]["plan_id"],body["plan_sha256"],method="user_ui")
    else:
        raise ConfigError("route","APIがありません",code="not_found")
    handler._send_json(HTTPStatus.OK,result)

def _workspace(handler,rid,cid):
    jobs,store=_context(handler)
    query=parse_qs(urlsplit(handler.path).query)
    current=store.current(rid,cid)
    try:
        config=_config(handler,rid,query)
    except ConfigError:
        if not current: raise
        config=None
    body=f'<h1>素材・骨格を整理する</h1><p><a href="/selected?run={U(rid)}">採用候補へ戻る</a></p><p data-story-message role="status"></p>'
    initial={"run_id":rid,"candidate_id":cid,"config_id":config}
    if not current:
        body+='<p>素材はまだありません。ログと設定から事実と構成案を整理します。</p>'
        if not config:
            options=''.join(f'<option value="{E(c["config_id"])}">{E(c["label"])}</option>' for c in jobs.configs.list())
            body+='<label>旧実験の文章化用設定<select data-story-config><option value="">選択してください</option>'+options+'</select></label>'
        body+='<button type="button" data-story-organize>素材・骨格を作る（LLM呼出しなし）</button>'
        return _doc(handler,"素材・骨格",body,initial)
    plan=current["plan"]; material=store.material(plan["material_ref"]["material_id"])
    initial.update(revision=plan["revision"],plan_sha256=current["plan_sha256"])
    byid={e["event_id"]:e for e in material["events"]}
    body+=f'<p>構成案 {plan["revision"]}版 · {"確認済み" if current["confirmed"] else "未確認"}</p>'
    body+='<section><h2>構成案</h2><label>主軸<input data-story-focus value="'+E(plan["focus"])+'"></label>'
    body+='<label>視点<input data-story-viewpoint value="'+E(plan["viewpoint"])+'"></label><label>語調<input data-story-tone value="'+E(plan["tone"])+'"></label>'
    body+="<p>本文の字数・段落数は制限しません。転機を中心に、出来事のつながりを物語として表現します。</p>"
    body+="<h3>物語の主軸</h3><ul>"+"".join("<li>"+E(t["question"])+"</li>" for t in plan.get("narrative_threads", []))+"</ul>"
    body+='<ol>'
    for beat in plan["beats"]:
        body+='<li><ul>'+''.join('<li>'+("<strong>転機</strong> " if i in plan["required_event_ids"] else "補助 ")+E(byid[i]["label"])+' <details><summary>根拠を読む</summary><pre>'+E(json.dumps(byid[i],ensure_ascii=False,indent=2))+'</pre></details></li>' for i in beat["event_ids"])+'</ul></li>'
    body+='</ol><details><summary>出来事の採用・圧縮・省略を編集する</summary><p>時系列を保持してください。省略する出来事はomissions配列を追加して理由を書きます。素材の事実欄は読み取り専用です。</p><textarea data-story-edit-json rows="20">'+E(json.dumps({"beats":plan["beats"],"required_event_ids":plan["required_event_ids"],"narrative_threads":plan.get("narrative_threads",[])},ensure_ascii=False,indent=2))+'</textarea></details>'
    body+='<button type="button" data-story-save>構成案を新しい版として保存</button> <button type="button" data-story-confirm>この骨格を確認する</button></section>'
    if current["confirmed"] and config:
        params=f'kind=narrate&pipeline=story_v1&candidate={U(cid)}'
        if config:params+='&config='+U(config)
        body+=f'<p><a class="button" href="/runs/{U(rid)}/generate?{params}">この骨格から本文を書く →</a></p>'
    hero=material["world"].get("protagonist")
    readable=[f for f in material["initial_facts"] if f["subject"]==hero or f["predicate"]=="relations"]
    body+='<section><h2>初期関係と所持品</h2><p>移動許可のcompanionsは仲間成立を示しません。仲間になる出来事は上の時系列にあります。</p><ul>'+''.join('<li>'+E(f["subject"])+': '+E(f["predicate"])+ ' '+E(json.dumps(f.get("value"),ensure_ascii=False))+'</li>' for f in readable)+'</ul></section>'
    body+='<section><h2>設定の詳細</h2><details><summary>設定の事実を読む</summary><pre>'+E(json.dumps(material["initial_facts"],ensure_ascii=False,indent=2))+'</pre></details></section>'
    body+='<section><h2>発動した効果・未解決事項</h2><p>条件の登録は、場面が起きた意味ではありません。</p><ul>'
    for e in material["pending_effects"]:
        label="発動済み" if e["status"]=="applied" else "未発動・未確認"
        body+='<li>'+E(e["library_id"] or e["effect_instance_id"])+'：'+label+'</li>'
    body+='</ul><details><summary>素材全体と抽出の限界</summary><pre>'+E(json.dumps(material["diagnostics"],ensure_ascii=False,indent=2))+'</pre></details></section>'
    body+=f'<p><a href="/api/stories/runs/{U(rid)}/{U(cid)}">全素材・構成案のJSONを読む</a></p>'
    _doc(handler,"素材・骨格",body,initial)

def _review_controls(saved, label):
    saved = saved or {}
    adoption = saved.get("adoption", "pending")
    choices = (("pending", "レビュー待ち"), ("accepted", "採用"),
               ("revise", "要修正"), ("rejected", "不採用"))
    options = ''.join('<option value="'+value+'"'+(' selected' if value == adoption else '')+'>'+name+'</option>'
                      for value, name in choices)
    body = '<section class="story-review"><h3>読後の評価</h3><p>各項目は1（低い）〜5（高い）。未評価のままでも保存できます。</p>'
    for key, label_text in (("causal_clarity", "出来事のつながり"),
                            ("story_flow", "物語としての流れ"),
                            ("surprise", "意外な展開")):
        selected = saved.get(key)
        score_options = '<option value="">未評価</option>' + ''.join(
            '<option value="'+str(n)+'"'+(' selected' if selected == n else '')+'>'+str(n)+'</option>'
            for n in range(1, 6))
        body += '<label>'+label_text+'<select data-story-score="'+key+'">'+score_options+'</select></label>'
    body += '<label>'+E(label)+'<select data-story-adoption>'+options+'</select></label>'
    body += '<label>評価メモ<textarea data-story-review-note>'+E(saved.get("note", ""))+'</textarea></label>'
    return body+'<button type="button" data-story-review>この本文への評価を保存</button></section>'


def _comparison_review_summary(store, manifest):
    summary = {}
    for row in manifest["entries"]:
        oid, cid = row["output_id"], row["candidate_id"]
        result = read_story_output(store, oid, cid)
        state = "本文なし" if not result or not result.get("text") else "レビュー待ち"
        if result and result.get("text"):
            folder = store.sink(oid, cid).folder
            try:
                pointer = read_json(folder/"story-review-pointer.json")
                saved = json.loads(verified(contained(folder, pointer["path"]), pointer["sha256"]))
                if (saved.get("text_sha256") == result["validation"]["text_sha256"]
                        and saved.get("plan_sha256") == result["validation"]["plan_sha256"]):
                    state = {"accepted":"採用", "revise":"要修正", "rejected":"不採用",
                             "pending":"評価保存済み"}.get(saved.get("adoption"), "評価保存済み")
            except FileNotFoundError:
                pass
        summary[row["blind_id"]] = state
    return summary

def _unused_old_review_controls(saved,label):
    saved=saved or {};adoption=saved.get("adoption","pending")
    choices=(("pending","レビュー待ち"),("accepted","採用"),("revise","要修正"),("rejected","不採用"))
    options=''.join('<option value="'+value+'"'+(' selected' if value==adoption else '')+'>'+name+'</option>' for value,name in choices)
    return '<label>'+E(label)+'<select data-story-adoption>'+options+'</select></label><label>評価メモ<textarea data-story-review-note>'+E(saved.get("note",""))+'</textarea></label><button type="button" data-story-review>この本文への評価を保存</button>'

def _transport_label(store,oid,cid,item):
    if item and item.get("status"):return item["status"]
    if (store.sink(oid,cid).folder/"call-started.json").exists():return "応答待ち（呼出し開始記録あり）"
    if (store.folder(oid)/"worker-started.json").exists():return "開始準備の記録あり"
    return "未開始"

def _comparison(handler,oid,cid):
    jobs,_=_context(handler)
    store=OutputStore(jobs.configs.control)
    req=store.request(oid);store.verify_artifacts(oid)
    if req.get("pipeline")!="story_v1" or cid not in req["candidate_ids"]:
        raise ConfigError("story","二段階方式の本文ではありません")
    item=store.sink(oid,cid).current()
    result=read_story_output(store,oid,cid)
    root=contained(store.folder(oid),"inputs/story/"+cid)
    material,plan=read_json(root/"materials.json"),read_json(root/"plan.json")
    body='<h1>本文と骨格を読み比べる</h1><p><a href="/outputs/'+U(oid)+'">生成記録へ</a></p><p data-story-message role="status"></p>'
    review=None
    if result:
        try:
            folder=store.sink(oid,cid).folder
            pointer=read_json(folder/"story-review-pointer.json")
            saved=json.loads(verified(contained(folder,pointer["path"]),pointer["sha256"]))
            if saved["text_sha256"]==result["validation"]["text_sha256"] and saved["plan_sha256"]==result["validation"]["plan_sha256"]:review=saved
        except FileNotFoundError:pass
    adoption=(review or {}).get("adoption","pending")
    adoption_label={"pending":"レビュー待ち","accepted":"採用","revise":"要修正","rejected":"不採用"}.get(adoption,"不明")
    body+='<p>生成：'+E(_transport_label(store,oid,cid,item))+' · 検査：'+E((result or {}).get("validation",{}).get("structural_status","未検査"))+' · 採否：'+E(adoption_label)+'</p>'
    body+='<button type="button" data-story-recheck>保存済み応答を再検査（再送なし）</button>'
    if result and result["text"]:
        body+='<div class="story-columns"><article><h2>本文</h2><div class="story-text">'+E(result["text"])+'</div></article>'
    else:
        body+='<div class="story-columns"><article><h2>本文</h2><p>読み取れる本文はまだありません。応答不明や形式不正の場合は生成記録を確認してください。</p>'
        body+='<button type="button" data-story-recheck>保存済み応答を再検査（再送なし）</button></article>'
    byid={e["event_id"]:e for e in material["events"]}
    body+='<aside><h2>固定した骨格</h2><ol>'+''.join('<li>'+E(byid[i]["label"])+'</li>' for b in plan["beats"] for i in b["event_ids"])+'</ol></aside></div>'
    if result:
        body+='<details><summary>検査結果と段落の根拠</summary><pre>'+E(json.dumps(result | {"text":None},ensure_ascii=False,indent=2))+'</pre></details>'
        body+=_review_controls(review,"採否")
    if review:
        body+='<p>保存した評価メモ：'+E(review["note"])+'</p>'
    _doc(handler,"本文と骨格",body,{"output_id":oid,"candidate_id":cid,
         "text_sha256":(result or {}).get("validation",{}).get("text_sha256")})

def _output_api(handler,parts,method):
    jobs,_=_context(handler)
    job_api.boundary(handler)
    body=handler._request_json()
    oid,cid,action=parts[3:6]
    store=OutputStore(jobs.configs.control);store.verify_artifacts(oid)
    if store.request(oid).get("pipeline")!="story_v1":
        raise ConfigError("story","二段階方式の本文ではありません")
    if action=="recheck" and body=={}:
        result=process_story_output(store,oid,cid)
    elif action=="review":
        required={"text_sha256","adoption","note"}
        optional={"causal_clarity","story_flow","surprise"}
        scores=[body.get(k) for k in optional]
        if (not required <= set(body) or set(body)-required-optional
                or body["adoption"] not in ("pending","accepted","revise","rejected")
                or not isinstance(body["note"],str) or len(body["note"])>20000
                or any(v is not None and (type(v) is not int or v not in range(1,6)) for v in scores)):
            raise ConfigError("review","評価形式が不正です")
        result=read_story_output(store,oid,cid)
        if not result or not result["text"] or result["validation"]["text_sha256"]!=body["text_sha256"]:
            raise ConfigError("review","本文の版が異なります",code="conflict")
        from execution.provenance import atomic_json
        import uuid,time
        result={"schema_version":1,**body,"reviewer":"user_ui","created_at":time.time(),
                "plan_sha256":result["validation"]["plan_sha256"]}
        folder=store.sink(oid,cid).folder
        name="story-review-"+uuid.uuid4().hex+".json"
        atomic_json(folder/name,result)
        atomic_json(folder/"story-review-pointer.json",{"path":name,"sha256":sha256(canonical(result))})
    else:
        raise ConfigError("request","評価または再検査の要求が不正です")
    handler._send_json(HTTPStatus.OK,result)

def _batch_comparison(handler,value):
    jobs,_=_context(handler)
    from scripts.story_compare import load
    folder,manifest=load(jobs.configs.control,value)
    query=parse_qs(urlsplit(handler.path).query)
    blind=query.get("work",[manifest["entries"][0]["blind_id"]])[0]
    row=next((r for r in manifest["entries"] if r["blind_id"]==blind),None)
    if not row:raise ConfigError("work","作品がありません",code="not_found")
    store=OutputStore(jobs.configs.control);oid,cid=row["output_id"],row["candidate_id"]
    request=store.request(oid)
    from execution.story_service import pinned_story
    material,plan,_=pinned_story(store.folder(oid),cid,request)
    if material["source_binding"]["source_log_sha256"]!=row["source_log_sha256"]:
        raise ConfigError("comparison","比較と元ログが一致しません")
    result=read_story_output(store,oid,cid);item=store.sink(oid,cid).current()
    saved_review=None
    try:
        pointer=read_json(store.sink(oid,cid).folder/"story-review-pointer.json")
        saved=json.loads(verified(contained(store.sink(oid,cid).folder,pointer["path"]),pointer["sha256"]))
        if result and saved["text_sha256"]==result["validation"]["text_sha256"] and saved["plan_sha256"]==result["validation"]["plan_sha256"]:
            saved_review=saved
    except FileNotFoundError:pass
    adoption={"pending":"レビュー待ち","accepted":"採用","revise":"要修正","rejected":"不採用"}.get((saved_review or {}).get("adoption"),"レビュー待ち")
    options=''.join('<option value="'+E(r["blind_id"])+'"'+(' selected' if r["blind_id"]==blind else '')+'>'+E(r["blind_id"])+'</option>' for r in manifest["entries"])
    summary=_comparison_review_summary(store,manifest)
    body='<h1>'+E(blind)+'：'+E(manifest.get("baseline_label","旧本文"))+'・新本文・骨格</h1><p>同じ候補の文章化を比較します。生成の成功と物語の採用は別です。条件名は読書画面に表示しません。</p>'
    body+='<form><label>作品<select name="work">'+options+'</select></label><button>この作品を読む</button></form><p data-story-message role="status"></p>'
    body+='<details><summary>全作品の評価状況</summary><ul>'+''.join('<li><a href="?work='+U(r["blind_id"])+'">'+E(r["blind_id"])+'</a>：'+E(summary[r["blind_id"]])+'</li>' for r in manifest["entries"])+'</ul></details>'
    body+='<p>新本文の取得：'+E(_transport_label(store,oid,cid,item))+' ／ 形式・参照：'+E((result or {}).get("validation",{}).get("structural_status","未検査"))+' ／ 利用者の採否：'+E(adoption)+'</p>'
    old=verified(contained(folder,row["old_file"]),row["old_text_sha256"]).decode("utf-8")
    body+='<div class="story-columns"><article><h2>'+E(manifest.get("baseline_label","旧本文"))+'</h2><div class="story-text">'+E(old)+'</div></article><article><h2>新本文</h2><div class="story-text">'+E((result or {}).get("text") or "読める本文はまだありません。取得・形式・検査の状態を確認してください。")+'</div></article></div>'
    byid={e["event_id"]:e for e in material["events"]}
    body+='<details><summary>この本文に使った骨格</summary><ol>'+''.join('<li>'+("転機：" if i in plan["required_event_ids"] else "補助：")+E(byid[i]["label"])+'</li>' for b in plan["beats"] for i in b["event_ids"])+'</ol></details>'
    if result:
        body+='<details><summary>検査の指摘</summary><ul>'+''.join('<li>'+E(f["message"])+'</li>' for f in result["validation"]["findings"])+'</ul><p>根拠IDの検査は、本文の意味の正しさを保証しません。</p></details>'
        body+=_review_controls(saved_review,"新本文の採否")
        if saved_review:
            body+='<p>保存した採否：'+E(adoption)+' ／ メモ：'+E(saved_review["note"])+'</p>'
    body+='<details><summary>今回の文章化の条件</summary><p>'+E(manifest["model"])+' ／ '+E(manifest["prompt_version"])+'</p><p>'+E(manifest["interpretation_limit"])+'</p></details>'
    _doc(handler,"作品の読み比べと評価",body,{"output_id":oid,"candidate_id":cid,
        "text_sha256":(result or {}).get("validation",{}).get("text_sha256")})

def dispatch(handler,parts,method):
    route=(parts and parts[0]=="stories") or parts[:2]==["api","stories"]
    if not route:return False
    try:
        if parts[:3]==["api","stories","outputs"] and len(parts)==6 and method=="POST":
            _output_api(handler,parts,method)
        elif parts[:2]==["api","stories"]:
            _api(handler,parts,method)
        elif len(parts)==4 and parts[1]=="outputs" and method=="GET":
            _comparison(handler,parts[2],parts[3])
        elif len(parts)==3 and parts[1]=="comparisons" and method=="GET":
            _batch_comparison(handler,parts[2])
        elif len(parts)==3 and method=="GET":
            _workspace(handler,parts[1],parts[2])
        elif len(parts)==1 and method=="GET":
            jobs,store=_context(handler)
            body='<h1>素材・骨格の一覧</h1><ul>'
            for r in store.list():
                body+=f'<li><a href="/stories/{U(r["run_id"])}/{U(r["candidate_id"])}">{E(r["candidate_id"])}</a> · {"確認済み" if r["confirmed"] else "未確認"}</li>'
            from scripts.story_compare import load
            comparison_root=store.root/"comparisons"
            body+='</ul><section><h2>文章化の比較と評価</h2><p>本文を読み、出来事のつながり・流れ・意外性と採否を保存できます。</p><ul>'
            if comparison_root.exists():
                for folder in sorted(comparison_root.iterdir()):
                    if not folder.is_dir():continue
                    try:
                        _,manifest=load(jobs.configs.control,folder.name)
                        body+='<li><a href="/stories/comparisons/'+U(folder.name)+'">'+E(folder.name)+'</a> · '+str(len(manifest["entries"]))+'作品</li>'
                    except (ConfigError,FileNotFoundError,OSError,ValueError,KeyError,TypeError):
                        body+='<li>'+E(folder.name)+' · 保存記録を確認できません</li>'
            body+='</ul></section>'
            _doc(handler,"素材・骨格",body)
        else:raise ConfigError("route","画面がありません",code="not_found")
    except ConfigError as error:
        job_api.send_error(handler,error)
    except FileNotFoundError:
        job_api.send_error(handler,ConfigError("story","保存記録がありません",code="not_found"))
    except (OSError,ValueError,TypeError,KeyError) as error:
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,ConfigError("story","保存記録を照合できません",code="storage_error").as_dict())
    return True
