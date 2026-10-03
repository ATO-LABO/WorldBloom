"""Compose from selected, chronological facts; full evidence stays immutable."""
import json
from gapengine.story_materials import verify_material
from gapengine.story_plan import validate_plan

VERSION = "story-narration-9"

def writer_event(e):
    d = e["details"]
    if e["verb"] == "fight":
        fact = f"{d.get('winner')}が{d.get('loser')}に勝った。戦利品は{d.get('loot',{})}。"
    elif e["verb"] == "give_item":
        fact = f"{e['actor_id']}が{d.get('target')}へ{d.get('item')}を{d.get('count')}個渡した。"
    elif e["verb"] in ("downed","revived","dead"):
        fact = e['actor_id'] + {"downed":"は戦闘不能になったが、この時点では生存。","revived":"は戦闘不能から復帰。","dead":"はこの時点で死亡した。"}[e["verb"]]
    elif e["verb"] == "payoff" and d.get("applied",{}).get("kind") == "stance":
        a=d["applied"]
        fact=f"{a.get('a')}から{a.get('b')}への関係が{'悪化' if a.get('delta',0)<0 else '改善'}した。知識の伝達方法や新たな行動は不明。"
    elif e["verb"] == "ally_gained":
        fact=f"{e['actor_id']}が{d.get('ally')}に仲間として関わる関係が成立した。逆向きの関係とは別。"
    elif e["verb"] == "move":
        fact=f"{e['actor_id']}が{d.get('origin')}から{d.get('dest')}へ移動した。"
    elif e["verb"] == "craft":
        fact=f"{e['actor_id']}が{d.get('consumed')}を使って{d.get('item')}を作った。"
    elif e["verb"] == "buy":
        fact=f"{e['actor_id']}が{d.get('spent')}を払って{d.get('item')}を買った。"
    elif e["verb"] == "ending":
        fact=f"{e['actor_id']}が目的物を届けた。物と届け先:{d.get('delivered')}。副筋の解決は不明。"
    else:
        fact = e["label"]
    keys = {"target","ally","item","count","origin","dest","route","consumed","spent",
            "winner","loser","loot","by","killer","transferred","delivered",
            "identity_seen","revealed","learned","disguise","appearance","neutralized",
            "disabled","sabotaged","misled","identity","belief","applied","library_id"}
    record = {k:e[k] for k in ("event_id","verb")}
    record["recorded_outcome"] = fact
    record["details"] = {k:v for k,v in d.items() if k in keys}
    # Preserve outcome fields needed to distinguish downed/dead/knowledge.
    record["facts"] = [{k:v for k,v in f.items() if k != "evidence_refs"} for f in e["facts"]
                       if f["predicate"] != "post_value" or any(x in f["arguments"][-1]
                       for x in ("vitality","knowledge","beliefs_about"))]
    if e["verb"] == "payoff":
        a = d.get("applied",{})
        if a.get("kind") == "modifier":
            record["rendering_limit"] = "戦闘能力への寄与だけ。具体的な援護攻撃、魔法や見えない力の出現は記録されていない。数値・仕組みを本文で説明しない。"
        elif a.get("kind") == "stance":
            record["rendering_limit"] = "aからbへの関係が変化した。deltaの負は悪化。伝聞、目撃、会話、復讐の行動、和解は記録されていない。"
    if e["verb"] == "downed":
        record["rendering_limit"] = "この時点は戦闘不能で、生存している。後の死亡をここへ繰り上げない。"
    if e["verb"] == "revived" and d.get("by") is None:
        record["rendering_limit"] = "戦闘不能から復帰した。救助者・治療の場面は不明。"
    return record

def build_story_prompt(material, plan):
    verify_material(material); validate_plan(material, plan)
    by_id = {e["event_id"]: e for e in material["events"]}
    selected = [by_id[i] for b in plan["beats"] for i in b["event_ids"]]
    aliases = {e["event_id"]: "r" + str(e["order"]) for e in selected}
    required = set(plan["required_event_ids"])

    def render(e):
        record = writer_event(e)
        record["event_id"] = aliases[e["event_id"]]
        record["actor_id"] = e["actor_id"]
        return record

    pivots = [render(e) for e in selected if e["event_id"] in required]
    linked_support = {i for link in material["causal_links"] if link["kind"] == "gift_to_ally"
                      for i in (link["from"], link["to"]) if i in aliases and i not in required}
    link_events = [e for e in selected if e["event_id"] in linked_support]
    # Complete at most two further gift/ally transitions; these show how the
    # party changed without making every logged ally a required scene.
    linked_support = {e["event_id"] for e in link_events[:4]}
    context = [e for e in selected if e["event_id"] not in required | linked_support
               and e["verb"] in {"buy", "craft", "trial", "sacrifice", "exposure"}]
    context = context[:4]
    support = [render(e) for e in selected if e["event_id"] in linked_support | {x["event_id"] for x in context}]
    hero = material["world"].get("protagonist")
    initial = []
    for f in material["initial_facts"]:
        if f["subject"] == hero and f["predicate"] in ("inventory", "goal", "knowledge"):
            initial.append({k: f[k] for k in ("subject", "predicate", "value")})
    linked = [{"kind": link["kind"], "from": aliases[link["from"]], "to": aliases[link["to"]]}
              for link in material["causal_links"] if link["from"] in aliases and link["to"] in aliases
              and (link["from"] in required or link["from"] in linked_support)
              and (link["to"] in required or link["to"] in linked_support)]
    packet = {
        "world": {k: material["world"].get(k) for k in ("name", "protagonist", "antagonist")},
        "entities": [{k: v for k, v in e.items() if k in ("id", "displayed")}
                     for e in material["entities"] if e["id"] in {hero, material["world"].get("antagonist")}
                     or any(e["id"] == x.get("actor_id") for x in pivots + support)],
        "initial_facts": [{k: v for k, v in f.items() if k != "evidence_refs"} for f in initial],
        "pivotal_events": pivots,
        "supporting_events": support,
        "confirmed_links": linked,
        "threads": [{"question": t["question"], "event_ids": [aliases[i] for i in t["event_ids"]]}
                    for t in plan.get("narrative_threads", [])],
        "required_event_ids": [aliases[i] for i in plan["required_event_ids"]],
        "life_timeline": [{"id": aliases[e["event_id"]], "actor": e["actor_id"], "state": e["verb"]}
                          for e in selected if e["event_id"] in required and e["verb"] in ("downed", "revived", "dead")],
        "constraints": plan["constraints"],
    }
    return f"""あなたは記録された展開から、読者のための日本語の物語を書く作家です。プロンプト版:{VERSION}
主軸:{plan['focus']}。視点:{plan['viewpoint']}。語調:{plan['tone']}。
まずthreadsの問いとpivotal_eventsから、人物の目的、行動、その結果、次に迫られる行動が読者に伝わる場面の流れを内部で組み立ててください。その後で本文を書いてください。
読者がその場に居合わせるように場面を描く。要約して出来事を列挙しない。人の身振り、距離、手に触れる物、動き、周囲の変化で場面を進める。場面の終わりが次の場面の状況を変えるようにする。視点人物が知らないことを知っていたように書かない。
「関係性が形成された」「契機となった」「目的を達成した」などの解説語だけで出来事を片づけず、実際に手渡す、倒れる、起き上がる、持ち帰る動作を書く。抽象的な総括や「物語は幕を閉じた」は本文から外す。
転機の前後を人物の行動や反応でつなぎ、一つの場面に複数の出来事を組み合わせてよい。文章の長さ、段落数、場面数に固定値はない。物語に必要なだけ書く。
全required_event_idsの転機を描く。supporting_eventsは関係や経緯を読者に分かるようにする必要があるときに使い、全件を列挙しない。採用した出来事は元ログの順序を守る。
confirmed_linksは記録から確認できる因果・成立関係だけを示す。時間が近いだけの出来事を原因と断定しない。記録にない理由や動機を作らず、理由が不明なままでも行動の前後が読めるように書く。
記録にない血縁の経緯、思い、会話、救助、敵意の理由を足さない。因果が不明な関係悪化は本文の主筋に混ぜない。登場人物の認識や感情を断定しない。
初期の同行許可と仲間成立は違う。贈与による仲間成立は、誰から誰に何が渡り、その後どう関係が変わったかを表現する。きびだんごは渡した向きを守る。
plantedは条件の登録で場面ではない。焚き火と約束を追加しない。payoffは実際に適用された作用だけを表現する。
正体の真実と人物が知っている正体を混同しない。downedは戦闘不能であり死亡ではない。deadだけが死亡。revivedは復帰で、記録に救助者がいなければ救助場面を創作しない。省いた戦闘があるので「二度目」「三度目」など総回数を断定しない。
勝者と敗者、得た物、関係が変化した相手を保持する。結末の目的達成を全員の和解に変えない。「一撃で」など攻撃方法や回数を作らない。
同じ出来事を繰り返し要約しない。本文に内部数値、根拠ID、記録・アルゴリズムの解説、未知の事柄を否定する説明を出さない。結末に和解の有無などを解説しない。風景や間は補えるが、未記録の攻撃、救済、過去、動機、結果を追加しない。
応答はJSONオブジェクト一つ。形式は{{"story":"物語の本文。\\n\\n次の段落。","used_event_ids":["r184","r185"]}}。storyは読者がそのまま読む文章とし、段落は自由に作る。used_event_idsには実際に本文で描いた出来事のIDを作品全体で列挙する。本文にIDや制作上の解説を入れない。

素材と構成（転機を中心に物語にする）：
{json.dumps(packet, ensure_ascii=False, separators=(',',':'))}

物語をstoryにして返してください。
"""
