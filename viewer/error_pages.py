"""Contextual HTML guidance. API error contracts remain JSON."""
from urllib.parse import urlsplit, unquote
from viewer import pages, review_pages as ui


def guidance(title, message, back='/', label='世界を選ぶ', *, name=None, phase='sifting', job_store=None, status=None):
    body='<div class="ux-shell ux-guidance">'+ui.heading('表示を確認',ui.link(back,'← '+label))
    body+='<section class="ux-error" role="alert"><span class="ux-error-icon" aria-hidden="true">ⓘ</span><div><h2>'+pages._escape(title)+'</h2><p>'+pages._escape(message)+'</p><div class="ux-links">'+ui.link(back,label+' →','ux-primary')+ui.link('/','ホームへ')+'</div>'
    if status:
        body+='<details><summary>詳しい情報</summary><p>HTTP '+str(int(status))+' · 元の設定や保存済みの結果を書き換える操作は行っていません。</p></details>'
    body+='</div></section></div>'
    return ui.doc(title,body,name,phase=phase,job_store=job_store)


def render(path,status,message,*,job_store=None):
    parts=[unquote(p) for p in urlsplit(path).path.split('/') if p]
    back,label,name,phase='/','世界を選ぶ',None,'world'
    if parts and parts[0]=='exp' and len(parts)>1:
        name=parts[1];phase='sifting';back='/exp/'+pages._url_segment(name);label='候補一覧へ戻る'
        if len(parts)>=5 and parts[2]=='cell':
            back+='/cell/'+pages._url_segment(parts[3]);label='候補の内容に戻る'
        elif len(parts)==2:back='/'
    elif parts and parts[0]=='runs' and len(parts)>1:
        phase='sifting';back='/runs/'+pages._url_segment(parts[1])+'/candidates';label='候補一覧へ戻る'
        if len(parts)>=5:
            back+='?candidate='+pages._url_segment(parts[3]);label='候補の内容に戻る'
        elif len(parts)<=3:back='/'
    elif parts and parts[0] in ('jobs','configs','history'):
        phase='run';back='/jobs' if parts[0]!='jobs' else '/';label='実行を確認' if back!='/ ' and back!='/' else '世界を選ぶ'
    elif parts and parts[0]=='outputs': phase='screening';back='/outputs' if len(parts)>1 else '/';label='作品一覧へ戻る' if len(parts)>1 else '世界を選ぶ'
    raw=bool(parts and parts[-1]=='raw')
    title='この候補の原記録を開けません' if raw else ('このページを開けません' if status!=400 else '表示する条件を確認してください')
    return guidance(title,message,back,label,name=name,phase=phase,job_store=job_store,status=status)
