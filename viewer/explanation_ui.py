"""Escaped HTML views for the four connected explanation fields."""
from html import escape
import json
from urllib.parse import quote

from gapengine.scenes import route_reason

LABELS = {"confirmed": "確認済み", "absent": "該当なし", "unknown": "不明"}
VERBS = {"rethink":"再考", "confront":"告発", "neutralize":"無効化", "payoff":"伏線回収", "observe":"観察",
         "move":"移動", "investigate":"調査", "give_item":"譲渡", "rest":"休息", "train":"訓練", "fight":"対決",
         "persuade":"説得", "pledge":"誓約", "mislead":"誘導", "grand_gesture":"大盤振る舞い", "share_knowledge":"情報共有",
         "concede":"譲歩", "craft":"作成", "disguise":"変装", "donate":"寄付",
         "guard":"守り", "negotiate":"交渉", "plant":"伏線設置", "rescue":"救助",
         "sabotage":"妨害", "sacrifice":"犠牲", "trial":"試練", "withdraw":"撤退",
         "labor":"労働", "buy":"購入",
         "respond":"現場での対処", "inspect_response":"現場の状況確認",
         "request_response":"対処の依頼", "answer_response":"依頼への返答",
         "submit_proposal":"原案提示", "present_proposal":"案の説明", "review_proposal":"賛否の表明",
         "amend_proposal":"案の修正", "request_decision":"判断の依頼", "decide_proposal":"案への判断"}


def e(value):
    return escape(str(value), quote=True)


def objective_outcomes(values):
    statuses = {'not_started':'判定前','pending':'未達成・継続中','achieved':'達成確定',
                'failed':'失敗確定','unknown':'観測不足で判定できない'}
    reasons = {'failure_condition':'失敗確定条件が成立','maintenance_breach':'維持する条件が崩れた',
               'achievement_condition':'達成条件が成立','period_maintained':'期間の終了まで維持',
               'deadline_unmet':'期限までに達成しなかった','incomplete_observation':'期間中の観測が不足',
               'period_not_observed':'判定期間を観測していない'}
    body = '<details open><summary>達成・失敗の判定（世界の実態）</summary><ul>'
    for value in values:
        period = value.get('period', {})
        body += '<li>'+e(value.get('label',value.get('condition','')))+'：'+e(statuses.get(value.get('status'),'不明'))
        body += ' ／ 判定期間 '+e(period.get('start','?'))+'〜'+e(period.get('end','?'))
        if value.get('reason'):body += ' ／ '+e(reasons.get(value['reason'],value['reason']))
        if value.get('decided_at') is not None:body += '（時点 '+e(value['decided_at'])+'）'
        body += '</li>'
    return body+'</ul><p class="ui-hint">人物がこの結果を知っているとは限りません。未達成と失敗確定を分けた記録です。</p></details>'


def verb_label(verb):
    # Short UI labels are intentionally separate from scenes.py prose templates.
    return VERBS.get(verb, f"未対応の行動（{verb}）")


def objective_choice_panel(choice):
    """Personal forecasts remain visibly separate from actual adjudication."""
    body = '<details open><summary>目的に沿った手段の比較</summary>'
    if choice.get('plan', {}).get('steps'):
        body += '<p class="ui-hint">後続の行動も完了した場合の予測です。</p>'
    if choice.get('consent_required'):
        body += '<p class="ui-notice">相手が同意した場合の見込みです。申し入れだけでは品物は移らず、目的も達成しません。</p>'
    if choice.get('answer') == 'refuse' or choice.get('refusal_basis'):
        body += '<p>申し入れへの返答は拒否です。品物は移りません。</p>'
    labels = {'true': '満たす見込み', 'false': '満たさない見込み', 'unknown': '情報が不足'}
    body += '<ul>'
    for value in choice.get('forecast', {}).get('entries', []):
        body += '<li>' + e(value['label']) + '：' + e(labels.get(value['state'], '不明'))
        body += ' ／ 優先順位 ' + e(value['priority']) + (' ／ 譲れない条件' if value['required'] else '') + '</li>'
    body += '</ul>'
    reasons = {'required_condition_conflict': '譲れない条件に反する',
               'higher_priority_harm': 'より優先する条件を損なう',
               'objective_time_or_terminal': '判定期間や確定した結果に合わない',
               'objective_failure_not_excluded': '失敗する条件を避けられる根拠がない',
               'transaction_unavailable': '取引の条件がそろわない',
               'objective_terminal': '本人は結果が確定したと認識している'}
    alternatives = [o for o in choice.get('alternatives', [])
                    if (o.get('excluded') or o.get('ranked_out'))
                    and not {'no_commitment', 'capability_unavailable'}.intersection(o.get('excluded', []))]
    if alternatives:
        body += '<p>選ばなかった手段</p><ul>'
        for option in alternatives:
            why = ('本人の優先順位で別の手段を選んだ' if option.get('ranked_out') else
                   '・'.join(dict.fromkeys(reasons.get(v.split(':')[0], '実行条件を満たさない') for v in option.get('excluded', []))))
            label = option.get('label') or ' / '.join(map(str, option.get('args', [])))
            body += '<li>' + e(verb_label(option['verb'])) + ' ' + e(label) + '：' + e(why) + '</li>'
        body += '</ul>'
    return body + '<p class="ui-hint">本人の情報と価値観に基づく予測です。相手の承諾や実際の達成とは区別します。</p></details>'


def planning_panel(plan):
    statuses = {'planned': '実行する手順が見つかった', 'conditional': '未確認の条件を含む計画',
                'awaiting_consent': '交換の返答を待っている', 'seeking_information': '情報を確かめに向かう',
                'no_known_plan': '今の情報では手順が見つからない', 'search_limit': '探索の上限まで調べた',
                'condition_met': '現在の達成条件は満たしている', 'inactive': 'この目的の計画は現在進めていない'}
    reasons = {'initial_plan': '最初の計画', 'previous_action_failed': '直前の行動が失敗したため再計画',
               'new_information': '認識した情報を更新', 'reply_received': '交換への返答を受けて見直し',
               'continuing': '現在の状況から残りの手順を確認'}
    body = '<details><summary>目的の分解と再計画（この行の詳細）</summary>'
    body += '<p>最終目的：' + e(plan.get('goal_label', '選んだ目的')) + '</p>'
    body += '<p>' + e(statuses.get(plan.get('status'), '計画の記録')) + ' ／ ' + e(reasons.get(plan.get('reason'), '')) + '</p>'
    if plan.get('assumptions'):
        body += '<p class="ui-notice">未確認の成立条件や相手の承諾を含みます。予定した結果が実現した記録ではありません。</p>'
    if plan.get('steps'):
        body += '<ol>'
        for step in plan['steps']:
            label = step.get('label', '')
            if step['verb'] == 'move': label = str(step['args'][0]) + 'へ移動する'
            elif step['verb'] == 'negotiate': label = str(step['args'][0]) + 'に' + str(step.get('meta', {}).get('objective', '品物')) + 'の交換を申し入れる'
            body += '<li>' + e(label) + ' ／ 必要な時点数 ' + e(step['duration']) + '</li>'
        body += '</ol>'
    if plan.get('status') in {'no_known_plan', 'search_limit'}:
        body += '<p>手順が見つからないことと、目的の失敗確定は分けて扱います。</p>'
    return body + '<p class="ui-hint">中間目標は人物の価値観として追加されません。これまでの出来事と資源の変化を残して計画を見直します。</p></details>'


# WB-ROUTE-001 S4 §1.1: reason badges for a policy.route record (or a
# scenes.py motive entry, same kind/cause/motive shape). Keyed the same way
# gapengine.route.annotate() sets "kind"/"cause" -- no new colors, just
# modifier classes on the existing ".tag" rule (see app.css ".tag.route-*").
_ROUTE_KIND_LABELS = {
    "advance": ("前進", "route-advance"),
    "prepare": ("準備", "route-prepare"),
    "lost": ("見通しなし", "route-lost"),
}
_ROUTE_DETOUR_LABELS = {
    "body": ("身体", "route-body"),
    "belief": ("思い込み", "route-belief"),
    "ignorance": ("手探り", "route-ignorance"),
    "none": ("理由なし", "route-none"),
}


def route_badge(route):
    """(label, css_class) for a route/motive record's kind+cause. Falls back
    to the motive id when an older run has no "motive_label" recorded."""
    kind = route.get("kind")
    cause = route.get("cause")
    if kind == "detour" and cause == "motive":
        label = route.get("motive_label") or route.get("motive") or "動機"
        return str(label), "route-motive"
    if kind == "detour":
        found = _ROUTE_DETOUR_LABELS.get(cause)
        if found:
            return found
    found = _ROUTE_KIND_LABELS.get(kind)
    if found:
        return found
    return (str(kind) if kind else "不明"), "route-unknown"


def action_text(item):
    return f"{item['subject']} が {verb_label(item['verb'])} {'・'.join(map(str,item['args']))}".strip()


def base_url(explanation):
    src = explanation["source"]
    return f"/exp/{quote(src['experiment'], safe='')}/cell/{quote(src['cell'], safe='')}"


def source_link(base, src, label="原ログ"):
    return f'<a href="{base}/raw?line={int(src["line"])}#L{int(src["line"])}">{e(label)} L{int(src["line"])}</a>'


def knowledge_text(grounds):
    known = grounds.get("knowledge", {})
    pieces = []
    for fact, belief in known.get("valued_beliefs", {}).items():
        if isinstance(belief, dict):
            label = {"culprit":"犯人", "weapon":"凶器"}.get(fact, fact)
            pieces.append(f"{label}: {belief.get('value', '不明')}（確信度 {belief.get('confidence', '不明')}）")
    for target, belief in known.get("belief", {}).items():
        if isinstance(belief, dict) and belief.get("known_modifiers"):
            pieces.append(f"{target} の既知の修飾子: {', '.join(belief['known_modifiers'])}")
    facts = known.get("evidence_used_by_rethink", known.get("knowledge", []))
    if facts:
        pieces.append(f"保持・参照した証拠 {len(facts)} 件（記録IDは詳細へ）")
    if not pieces and known:
        pieces.append("記録された他者の見積もりあり（詳細を参照）")
    return "／".join(pieces) if pieces else grounds["text"]


def turning_text(turning):
    text = turning["text"]
    for verb, label in VERBS.items():
        text = text.replace(" の " + verb + " ", " の " + label + " ")
    return text


def link_text(link):
    after = link.get("after") or {}
    before = link.get("before") or {}
    if "value" in after:
        return f"見立て: {before.get('value', 'なし')} → {after['value']}"
    if after.get("mode") == "chosen":
        return "後に回収された伏線が、この選択で追加された"
    return "後続行動に必要な条件: 不成立 → 成立"


def short(explanation):
    item = explanation.get("representative")
    if not item:
        return '<p class="muted">選択の記録がなく不明</p>'
    return (f'<p class="explain-short"><strong>T{e(item["turn"])} {e(action_text(item))}</strong><br>'
            f'根拠: {e(knowledge_text(item["grounds"])[:140])}<br>'
            f'代償: {e(item["cost"]["text"])}<br>転機: {e(turning_text(item["turning"]))}</p>')


def panel(explanation, item=None, *, details=True):
    item = item or explanation.get("representative")
    if not item:
        return '<p class="muted">四項目は記録不足で不明</p>'
    base = base_url(explanation)
    choice, grounds, cost, turning = [item[k] for k in ("choice", "grounds", "cost", "turning")]
    def entry(label, value, text, *, turning=False):
        tag = LABELS.get(value["status"], "不明")
        if turning:
            tag = {**LABELS, "candidate": "候補"}.get(value.get("confirmation"), "不明")
        return f'<dt>{label} <span class="tag">{tag}</span></dt><dd>{e(text)}</dd>'
    body = '<dl class="four-items">'
    body += entry("選択", choice, f"T{item['turn']} {action_text(item)}／場所: {choice.get('zone') or '不明'}")
    route = ((item.get("system") or {}).get("policy") or {}).get("route")
    if route:
        # WB-ROUTE-001 S4 §1.2: one line naming the道筋 (route) kind/cause
        # right after the choice itself. explanation-dump count is untouched.
        label, css_class = route_badge(route)
        # WB-ROUTE-001 S4 review: reuse scenes.py's route_reason so the
        # four-item panel and the scene timeline never disagree on the same
        # decision's reason (lost/detour-none get the same override here).
        why = route_reason(route) or "はっきりした理由は記録されていない"
        milestone = route.get("milestone")
        milestone_suffix = f"（次の節目: {e(milestone)}）" if milestone else ""
        body += (
            f'<p class="route-line">道筋: <span class="tag {css_class}">{e(label)}</span> '
            f'{e(why)}{milestone_suffix}</p>'
        )
    body += entry("根拠", grounds, knowledge_text(grounds))
    body += entry("即時の代償", cost, cost["text"])
    body += entry("転機", turning, turning_text(turning), turning=True)
    body += '</dl><p class="muted">知識の一覧は記録された範囲に限ります。知識があったことと心理的な動機は区別します。遅延した代償は未確認。</p>'
    response=(item.get('outcome',{}).get('details') or {}).get('response')
    if response:
        decision=response.get('choice_basis') or response.get('assessment') or {}
        signal=decision.get('signal') or response.get('signal') or response.get('observation') or {}
        states={'danger':'危険の知らせを信じている','clear':'危険は解消したと認識している',
                'unknown':'知らせを持っていない','stale':'知らせが古い','uncertain':'確信が足りない',
                'objective':'本人が重視する目的から行動している','objective_terminal':'本人は結果が確定したと認識している'}
        body+='<details open><summary>守りたい条件に基づく行動</summary>'
        if decision.get('condition'): body+='<p>対象の条件：'+e(decision['condition'])+'</p>'
        if response.get('label'):body+='<p>行動：'+e(response['label'])+'</p>'
        if decision.get('objective_forecast'):
            forecast=decision['objective_forecast']
            labels={'true':'成立する予測','false':'成立しない予測','unknown':'判断する情報が不足'}
            body+='<p>本人の見込み：達成条件は'+e(labels[forecast['success']['state']])+' ／ 失敗確定条件は'+e(labels[forecast['failure']['state']])+'</p>'
        if signal:
            body+='<p>'+e(states.get(signal.get('status'),signal.get('status','不明')))
            if signal.get('source') is not None: body+='・情報源 '+e(signal['source'])
            if signal.get('observed_at') is not None: body+='・観測時点 '+e(signal['observed_at'])
            body+='</p>'
        if decision.get('eta') is not None: body+='<p>移動と残りの作業に必要な時点数：'+e(decision['eta'])+'</p>'
        if response.get('progress') is not None:
            body+='<p>現場での作業：'+e(response['progress'])+' / '+e(response['steps'])+'</p>'
        if response.get('answer'): body+='<p>依頼への返答：'+e({'accept':'受諾','refuse':'拒否'}.get(response['answer'],response['answer']))+'</p>'
        reasons={'no_commitment':'本人が重視する条件に含まれない','unknown':'知らせがない','stale':'知らせが古い',
                 'uncertain':'情報への確信が不足','clear':'危険は解消したという認識','unreachable':'現場への経路がない',
                 'too_late':'期限に間に合わない','missing_resources':'所持品が不足','missing_evidence':'根拠の情報が不足',
                 'insufficient_stamina':'体力が不足','capability_unavailable':'実行できる役割ではない',
                 'response_verb_unavailable':'対処行動が使えない','response_phase_unavailable':'この時点では対処できない',
                 'required_condition_conflict':'別の譲れない条件に反する','protection_not_justified':'保護できる予測がない',
                 'known_precondition_failed':'知っている実行条件を満たさない',
                 'objective_terminal':'本人の認識では目的の結果が確定している',
                 'objective_not_started':'判定期間が始まっていない','objective_too_late':'判定期間に間に合わない',
                 'objective_not_justified':'達成と失敗回避の見込みを確認できない'}
        excluded=[o for o in decision.get('alternatives',[]) if o.get('excluded')]
        if excluded:
            body+='<ul>'
            for option in excluded:
                body+='<li>'+e(option['action'])+'：'+e('・'.join(reasons.get(v,v) for v in option['excluded']))+'</li>'
            body+='</ul>'
        body+='<p class="ui-hint">帰還、依頼、受諾、実行完了を区別した記録です。</p></details>'
        if response.get('world_report'):
            body+='<details><summary>行動後の実際の条件</summary><ul>'
            for value in response['world_report']:
                body+='<li>'+e(value['label'])+'：'+e({'true':'満たしている','false':'満たしていない','unknown':'不明'}[value['state']])+'</li>'
            body+='</ul><p class="ui-hint">作者向けの実態です。本人の認識と異なる場合があります。</p></details>'
    detail=item.get('outcome',{}).get('details') or {}
    if detail.get('objective_choice'):
        body += objective_choice_panel(detail['objective_choice'])
    if detail.get('planning'):
        body += planning_panel(detail['planning'])
    outcomes=detail.get('objective_outcomes') or ([detail['objective_outcome']] if detail.get('objective_outcome') else [])
    if outcomes:body+=objective_outcomes(outcomes)
    event=detail.get('deliberation_event',{})
    basis=event.get('choice_basis',{})
    valuation=event.get('concern_assessment')
    if not valuation and basis.get('current_concerns'):
        selected=next((o for o in basis.get('options',[]) if o['verb']==basis['chosen']['verb'] and o['args']==basis['chosen']['args']),{})
        valuation=selected.get('own_assessment',{}).get('concern_assessment') or basis['current_concerns']
    if valuation:
        body+='<details open><summary>この案で守れそうなこと（本人の認識）</summary><ul>'
        labels={'true':'条件を満たす予測','false':'条件を満たさない予測','unknown':'判断する情報が不足'}
        for entry in valuation['entries']:
            body+=f'<li>{e(entry["label"])}：{e(labels[entry["state"]])}・優先順位 {int(entry["priority"])}'+('・譲れない条件' if entry['required'] else '')+'</li>'
        body+='</ul><p class="ui-hint">案を実施した場合の見込みです。承認や現実の達成を意味しません。</p></details>'
    if event.get('world_concerns'):
        body+='<details><summary>記録時点の実際の状態</summary><ul>'
        labels={'maintained':'維持できている','breached':'維持できていない','achieved':'実現している','pending':'まだ実現していない','unknown':'不明'}
        for entry in event['world_concerns']:
            body+=f'<li>{e(entry["label"])}：{e(labels[entry["progress"]])}</li>'
        body+='</ul><p class="ui-hint">作者向けの記録です。本人がこの情報を知っているとは限りません。</p></details>'
    body += '<p>' + source_link(base, item["source"], "選択・結果")
    for src in grounds.get("sources", []):
        body += ' · ' + source_link(base, src, "知識")
    for c in cost.get("items", []):
        if c.get("before_source"):
            body += " · " + source_link(base, c["before_source"], "代償の直前値")
    for src in turning.get("search", {}).get("candidate_sources", []):
        if src["line"] != item["line"]:
            body += ' · ' + source_link(base, src, "転機候補イベント")
    body += '</p>'
    if cost.get("status") == "confirmed" and not cost.get("complete", False):
        body += '<p class="muted">表示した損失は確認済みですが、ほかの即時の代償には不明な部分があります。</p>'
    for link in turning.get("links", []):
        body += (f'<p class="causal-link">{e(link_text(link))} · '
                 f'{source_link(base,link["source"],"変更")} → {source_link(base,link["downstream"],"後続行動")}</p>')
    # Keep recorded alternatives visible; details only controls JSON dumps.
    alternatives = choice["alternatives"]
    body += '<details><summary>候補・抽選条件（本人の根拠とは別）</summary>'
    if alternatives["status"] == "confirmed":
        body += f'<p>記録 {alternatives["recorded_candidates"]} / 全 {alternatives["total_candidates"]} 件。省略: {"あり" if alternatives["truncated"] else "なし"}。確率は全候補に対する値。</p>'
        body += f'<p>選別規則: {e(alternatives["rule"])}／フォールバック: {e(alternatives["fallback"])}</p>'
        body += '<div class="table-scroll"><table><thead><tr><th>行動</th><th>対象</th><th>重み</th><th>確率</th><th>実行</th></tr></thead><tbody>'
        for c in alternatives["candidates"]:
            body += f'<tr><td>{e(verb_label(c["verb"]))}</td><td>{e("・".join(map(str,c["args"])))}</td><td>{e(c["weight"])}</td><td>{e(c["probability"])}</td><td>{"選択" if c["selected"] else ""}</td></tr>'
        body += '</tbody></table></div>'
    else:
        body += f'<p>{e(alternatives["text"])}</p>'
    body += f'<p>{e(item["system"]["text"])}</p>'
    if details:
        body += f'<pre class="explanation-dump">{e(json.dumps(item["system"],ensure_ascii=False,indent=2))}</pre>'
    body += '</details>'
    if not details:
        return body
    body += '<details><summary>知識・状態変化・結果と出典の詳細</summary>'
    body += f'<pre class="explanation-dump">{e(json.dumps({k:item[k] for k in ("grounds","cost","outcome","turning")},ensure_ascii=False,indent=2))}</pre></details>'
    return body
