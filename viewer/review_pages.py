"""Shared reading workspaces for standalone and read-only routes."""
import json
from types import SimpleNamespace
from hashlib import sha256
from viewer import pages, data, reader_ui, explanation_ui

E, U = pages._escape, pages._url_segment
ASSETS = '<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/review-workspace.css"><script src="/static/review-workspace.js" defer></script>'


def doc(title, body, name=None, *, job_store=None, world=None, output_run=None, phase="sifting", phases=None):
    return pages.document(title, body, run=name, world=world, output_run=output_run,
        phase=phase, phases=phases, job_store=job_store, page_class="run-observer").replace('</head>',ASSETS+'</head>')


def link(href, label, cls=""):
    return f'<a class="{cls}" href="{E(href)}">{E(label)}</a>'


def heading(title, back, actions="", badge=""):
    return f'<header class="ux-heading"><div>{back}<h1>{E(title)} {badge}</h1></div><nav>{actions}</nav></header>'


def tabs():
    return '<nav class="ux-tabs" role="tablist" aria-label="表示する情報">'+''.join(
        f'<button type="button" role="tab" id="ux-tab-{key}" data-ux-tab="{key}" aria-controls="ux-panel-{key}" aria-selected="{str(key=="story").lower()}" tabindex="{0 if key=="story" else -1}">{label}</button>'
        for key,label in (("story","物語"),("reason","選択と根拠"),("data","実験データ")))+'</nav>'


def panel(key, body):
    return f'<section id="ux-panel-{key}" role="tabpanel" aria-labelledby="ux-tab-{key}" data-ux-panel="{key}"'+('' if key=='story' else ' hidden')+'>'+body+'</section>'


def summary(model, cell):
    explanation = model.get("explanation") or {}
    reader = explanation.get("reader_summary")
    entry = model.get("synopsis") or {}
    title = str(entry.get("title") or f"区画 {cell.replace('|',' / ')} の物語")
    prose = entry.get("synopsis") if entry.get("status") == "ok" else ""
    label = "保存されたあらすじ（AI生成・未照合）"
    if reader:
        title = reader["summary"]["title"]["text"]
        if not prose:
            prose = '\n\n'.join(p['text'] for p in reader['summary']['synopsis'])
            label = reader_ui._short_label(reader)
    return title, str(prose or ""), label


def metrics(model, cell):
    return '<dl class="ux-facts">'+''.join(f'<dt>{E(k)}</dt><dd>{E(v)}</dd>' for k,v in (
        ("区画",cell),("世代",model.get('generation',"—")),("品質",f'{model["quality"]:.4f}'),
        ("結末への到達",pages._reached_seeds(model['reach_rate'],len(model.get('seeds') or []))),
        ("seed",model.get('seed',"—"))))+'</dl>'


def candidate(repo, name, cell, model, story, reason, technical, nav_links, *, job_store=None):
    base=f'/exp/{U(name)}/cell/{U(cell)}'; grid=f'/exp/{U(name)}'
    title, prose, label = summary(model,cell)
    binding=None; item=None; rid=None
    if repo.catalog is not None and job_store is not None:
        from viewer import sifting_view
        rid=repo.catalog.register_legacy(name)
        loaded=sifting_view.load(SimpleNamespace(repository=repo,server=SimpleNamespace(job_store=job_store)),rid)
        cid=loaded['reps'].get(cell)
        item=next((c for c in loaded['items'] if c['candidate_id']==cid),None)
        if item:
            if model['explanation']['source']['sha256'] != item.get('source_log_sha256'):
                raise data.BadRequest('候補の原記録が更新されています。候補一覧から選び直してください。')
            latest=repo.catalog.snapshot(rid)
            if latest['revision']!=loaded['publication'] or (loaded['legacy'] and sha256(repo.safe_path(repo.experiment(name),'archive.json').read_bytes()).hexdigest()!=latest['manifest'].get('source_archive_sha256')):
                raise data.BadRequest('候補の公開版が更新されています。候補一覧から選び直してください。')
            if item['synopsis']: prose,label=item['synopsis'],'保存されたあらすじ（AI生成・未照合）'
            title=item.get('title') or title
            binding={'run_id':rid,'candidate_id':cid,'revision':loaded['revision'],'running':loaded['running']}
            grid=f'/runs/{U(rid)}/candidates?candidate={U(cid)}'
    raw=item['raw_href'] if item and item['raw_href'] else base+'/raw'
    # Legacy output cards are replaced by a single readable synopsis; keep timeline and saved prose accessible.
    flow=story[story.index('<section class="card story-section">'):]
    synopsis=f'<section class="ux-prose"><h3>あらすじ</h3><p>{E(prose or "あらすじはまだ保存されていません。下の記録から物語の流れを確認できます。")}</p><small>{E(label) if prose else ""}</small></section>'
    for cap,entry in (('あらすじ',model.get('synopsis')),('本文',model.get('story'))):
        if isinstance(entry,dict) and entry.get('error'): synopsis+=f'<p class="ux-muted">{cap}の生成は完了していません（{E(entry.get("status","—"))}）：{E(entry["error"])}</p>'
    if model.get('story_text'):
        synopsis+='<details><summary>保存された本文を読む</summary><p class="ux-prose">'+E(model['story_text'])+'</p></details>'
    decision=''
    if binding:
        disabled=' disabled' if binding['running'] else ''
        decision='<section class="ux-decision"><fieldset'+disabled+'><legend>この候補の判定</legend>'
        for state,cap in (("unclassified","未分類"),("adopted","採用"),("held","保留"),("rejected","除外")):
            decision+=f'<label><input type="radio" name="verdict" value="{state}"'+(' checked' if item['state']==state else '')+(' disabled' if state=='adopted' and not item['screenable'] else '')+f'>{cap}</label>'
        decision+='</fieldset><label class="ux-field">選ぶ理由・メモ<textarea data-ux-note rows="2" maxlength="8000"'+disabled+'>'+E(item['note'])+'</textarea></label>'
        decision+='<p role="status" data-ux-save>'+('実行中は判定・メモを変更できません' if binding['running'] else '判定・メモは自動保存')+'</p><button type="button" data-ux-reload hidden>保存状況を確認</button></section>'
    else:
        decision='<p class="ux-muted">閲覧専用：保存された物語と記録を表示しています。</p>'
    body='<div class="ux-shell ux-candidate" data-reading'+(f' data-candidate-binding="{E(json.dumps(binding))}"' if binding else '')+'>'
    body+=heading('候補の物語を読む',link(grid,'← 候補一覧へ'),' '.join(nav_links))
    body+='<div class="ux-columns"><article class="ux-reading"><h2>'+E(title)+'</h2>'+tabs()+'<div class="ux-scroll">'
    body+=panel('story',synopsis+flow)+panel('reason',reason)+panel('data',technical)+'</div>'+decision+'</article>'
    body+='<aside class="ux-aside ux-scroll"><h2>この候補の記録</h2>'+metrics(model,cell)+'<p class="ux-muted">品質は実験の評価値です。物語の読みやすさとは別に確認してください。</p><div class="ux-links">'+link(base+'/lineage','転機と系譜を見る →')+link(raw,'原記録を確認 →')+'</div></aside></div>'
    body+=('<footer class="ux-footer"><span>保存された候補を表示しています</span>'+link('/selected?run='+U(rid),'採用候補を確認 →','ux-primary')) if binding else ('<footer class="ux-footer"><span>閲覧専用</span>'+link(grid,'格子に戻る →'))
    return doc('候補の物語を読む',body+'</footer></div>',name,job_store=job_store,output_run=rid,phases=data.phase_status(repo,name,job_store=job_store))


def grid(repo, name, *, job_store=None):
    root=repo.experiment(name); archive=repo.archive(root); cells=data._as_mapping(archive.get('cells'))
    meta=data.experiment_meta(repo,root); cats,bins=data._ordered_axes(meta['categories'],meta['bins'],cells)
    synopses=data.synopsis_texts(repo,root); base=f'/exp/{U(name)}'; previews=[]; rows=[]
    for cat in cats:
        columns=[]
        for bin_name in bins:
            cell=f'{cat}|{bin_name}'; elite=cells.get(cell)
            if not isinstance(elite,dict): columns.append('<td class="ux-empty">—</td>');continue
            ident=len(previews); text=synopses.get(cell,''); title=(text.splitlines()[0][:48] if text else f'区画 {cell.replace("|"," / ")}')
            quality=elite.get('quality'); q=f'{quality:.4f}' if isinstance(quality,(int,float)) else '—'
            try: explanation=data.cell_explanation(repo,root,cell)
            except (data.MissingResource,FileNotFoundError): explanation=None; four_items='<p class="ux-muted">原ログが見つからないため四項目を表示できません。</p>'
            else: four_items=reader_ui.short(explanation)
            if explanation and explanation.get('reader_summary'): title=explanation['reader_summary']['summary']['title']['text']
            columns.append(f'<td><button type="button" data-preview="{ident}" aria-pressed="{str(ident==0).lower()}"><strong>{E(title)}</strong><small>品質 {q}</small></button><label class="ux-pick"><input type="checkbox" form="ux-compare" name="cell" value="{E(cell)}">比較に追加</label></td>')
            previews.append(f'<article data-preview-panel="{ident}"'+('' if ident==0 else ' hidden')+f'><h2>{E(title)}</h2><p class="ux-muted">{E(cell)} · 世代 {E(elite.get("generation","—"))} · 品質 {q} · 到達 {E(pages._reached_seeds(data._number(elite.get("reach_rate")),len(meta.get("seeds") or [])))}</p><p class="ux-prose">{E(text or "あらすじは保存されていません。候補の詳細で原記録を確認できます。")}</p>'+four_items+'<div class="ux-links">'+link(base+'/cell/'+U(cell),'候補を詳しく読む →','ux-primary')+link(base+'/cell/'+U(cell)+'/lineage','系譜を見る')+link(base+'/cell/'+U(cell)+'/raw','原記録を見る')+'</div></article>')
        rows.append(f'<tr><th scope="row">{E(cat)}</th>'+''.join(columns)+'</tr>')
    body='<div class="ux-shell" data-readonly-grid>'+heading('格子で物語を探す',link('/','← 世界を選ぶ'),link(base+'/river','系譜の川を見る'),'<small class="ux-badge">閲覧専用</small>')
    body+='<div class="ux-toolbar"><p>保存された代表候補を閲覧しています</p><label>候補を探す <input type="search" data-grid-search placeholder="区画・あらすじ"></label><button type="button" data-grid-mode aria-pressed="false">一覧で表示</button></div>'
    body+='<div class="ux-columns"><div class="ux-scroll ux-grid"><table><thead><tr><th>区画</th>'+''.join(f'<th scope="col">{E(b)}</th>' for b in bins)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table>'+('' if previews else '<p class="ux-empty">候補がまだありません。</p>')+'</div><aside class="ux-aside ux-scroll">'+''.join(previews)+'</aside></div>'
    body+=f'<form id="ux-compare" class="ux-footer" action="{base}/compare" method="get"><span data-compare-count>比較対象を2〜4件選んでください</span><span class="ux-muted">比較の選択は保存されません</span><button class="ux-primary" type="submit">選んだ候補を比較 →</button></form></div>'
    return doc('格子で物語を探す',body,name,job_store=job_store,world=pages._experiment_world(meta,job_store),phases=data.phase_status(repo,name,job_store=job_store))


def compare(repo,name,cells,*,job_store=None):
    base=f'/exp/{U(name)}'
    if not 2<=len(cells)<=4 or len(set(cells))!=len(cells):
        from viewer.error_pages import guidance
        return guidance('比較する候補を選んでください','異なる候補を2〜4件選ぶと、物語を並べて確認できます。',base,'格子で候補を選ぶ',name=name,job_store=job_store)
    root=repo.experiment(name); cards={k:[] for k in ('story','reason','data')}
    explanations=[]
    for cell in cells:
        m=data.cell_view(repo,root,cell,view='digest'); title,prose,label=summary(m,cell); ex=m['explanation']; explanations.append(ex)
        head=f'<header><h2>{E(title)}</h2><p class="ux-muted">{E(cell)} · 世代 {E(m["generation"])}</p></header>'
        tail='<div class="ux-links">'+link(base+'/cell/'+U(cell),'候補の詳細 ↗')+link(base+'/cell/'+U(cell)+'/raw','原記録 ↗')+'</div>'
        cards['story'].append('<article>'+head+'<h3>あらすじ</h3><p class="ux-prose">'+E(prose or 'あらすじはまだ保存されていません。原記録から内容を確認できます。')+'</p><small>'+E(label if prose else '')+'</small><h3>物語の流れ</h3>'+pages._timeline(m)+tail+'</article>')
        cards['reason'].append('<article>'+head+reader_ui.panel(ex)+tail+'</article>')
        cards['data'].append('<article>'+head+metrics(m,cell)+pages._genome_panel(m['genome'],m['categories'])+tail+'</article>')
    same=len({x['trajectory_signature'] for x in explanations})==1
    diagnostic='主人公の行動・対象・結果の並びは同じ筋です。' if same else '主人公の行動・対象・結果の並びに差があります。物語品質の優劣は判定していません。'
    body='<div class="ux-shell ux-compare" data-reading>'+heading('物語を並べて読む',link(base,'← 格子に戻る'),f'{len(cells)}件を比較中','<small class="ux-badge">閲覧専用</small>')+tabs()+'<div class="ux-scroll">'
    for key,columns in cards.items():
        body+=panel(key,f'<div class="ux-comparison" style="--ux-count:{len(cells)}">'+''.join(columns)+'</div>')
    body+='</div><footer class="ux-footer"><span>'+E(diagnostic)+'</span>'+link(base,'比較対象を選び直す')+'</footer></div>'
    return doc('物語を並べて読む',body,name,job_store=job_store,phases=data.phase_status(repo,name,job_store=job_store))


def river(repo,name,*,selected_cell=None,job_store=None):
    from viewer import lineage_river as lr
    model=lr.river_model(repo,name,selected_cell=selected_cell); base=f'/exp/{U(name)}'; url=base+'/river'
    rendered=lr.render_river(model,url)
    # Keep the proven graph and its dense-run limits; expose controls separately.
    start=rendered.index('<div class="grid-wrap river-wrap">'); stop=rendered.index('</div>',start)+6
    graph=rendered[start:stop]; legend=rendered[stop:]
    options='<option value="">すべての系譜</option>'+''.join(f'<option value="{E(c)}"'+(' selected' if c==selected_cell else '')+f'>{E(c)}</option>' for c in sorted(model['elite_nodes']))
    body='<div class="ux-shell ux-river" data-river-workspace>'+heading('物語の系譜をたどる',link(base,'← 候補一覧へ'),'<span class="ux-muted">系譜の川</span>')
    body+=f'<div class="ux-toolbar"><form method="get" action="{url}"><label>強調する系譜 <select name="cell">{options}</select></label><button type="submit">表示</button></form><label><input type="checkbox" data-river-only'+('' if selected_cell else ' disabled')+'> 選んだ系譜だけ</label><div><button type="button" data-zoom="fit">全体を表示</button><button type="button" data-zoom="out" aria-label="縮小">−</button><output data-zoom-level>全体</output><button type="button" data-zoom="in" aria-label="拡大">＋</button></div></div>'
    body+='<div class="ux-columns"><section class="ux-river-canvas">'+graph+'</section><aside class="ux-aside ux-scroll"><h2>選択した候補</h2>'
    if selected_cell:
        node=model['index'][model['elite_nodes'][selected_cell]]
        body+=f'<h3>{E(selected_cell)}</h3><dl class="ux-facts"><dt>品質</dt><dd>{model["elite_quality"][selected_cell]:.4f}</dd><dt>記録された世代</dt><dd>{E(model["elite_nodes"][selected_cell][0])}</dd></dl>'
        body+='<div class="ux-links">'+link(base+'/cell/'+U(selected_cell)+'/lineage','この候補の転機を見る →','ux-primary')+link(base+'/cell/'+U(selected_cell),'候補の物語を読む')+'</div><details><summary>個体の記録</summary><pre>'+E(json.dumps(node,ensure_ascii=False,indent=2,default=str))+'</pre></details>'
    else:
        body+='<p>上の一覧または図の区画名を選ぶと、その候補に続く系譜を強調します。</p><div class="ux-links">'+''.join(link(url+'?cell='+U(c),c) for c in sorted(model['elite_nodes']))+'</div>'
    counts=model['counts']
    body+='</aside></div><footer class="ux-footer"><span>'+f'{counts["generations"]}世代 · {counts["total"]}個体 · 親を確認できない参照 {counts["unresolved"]}本'+'</span>'+legend+'</footer></div>'
    return doc('物語の系譜をたどる',body,name,job_store=job_store,phases=data.phase_status(repo,name,job_store=job_store))
