"""Escaped HTML views for the four connected explanation fields."""
from html import escape
import json
from urllib.parse import quote

LABELS = {"confirmed": "確認済み", "absent": "該当なし", "unknown": "不明"}
VERBS = {"rethink":"再考", "confront":"告発", "neutralize":"無効化", "payoff":"伏線回収", "observe":"観察",
         "move":"移動", "investigate":"調査", "give_item":"譲渡", "rest":"休息", "train":"訓練", "fight":"対決",
         "persuade":"説得", "pledge":"誓約", "mislead":"誘導", "grand_gesture":"大盤振る舞い", "share_knowledge":"情報共有"}


def e(value):
    return escape(str(value), quote=True)


def verb_label(verb):
    # Short UI labels are intentionally separate from scenes.py prose templates.
    return VERBS.get(verb, f"未対応の行動（{verb}）")


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
    body += entry("根拠", grounds, knowledge_text(grounds))
    body += entry("即時の代償", cost, cost["text"])
    body += entry("転機", turning, turning_text(turning), turning=True)
    body += '</dl><p class="muted">知識の一覧は記録された範囲に限ります。知識があったことと心理的な動機は区別します。遅延した代償は未確認。</p>'
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
    if not details:
        return body
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
    body += f'<p>{e(item["system"]["text"])}</p><pre>{e(json.dumps(item["system"],ensure_ascii=False,indent=2))}</pre></details>'
    body += '<details><summary>知識・状態変化・結果と出典の詳細</summary>'
    body += f'<pre>{e(json.dumps({k:item[k] for k in ("grounds","cost","outcome","turning")},ensure_ascii=False,indent=2))}</pre></details>'
    return body
