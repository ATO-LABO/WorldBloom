"""Code-owned action inventory and authored stamina adjustments."""
from functools import lru_cache
from pathlib import Path
import yaml
from engine.stamina import action_verbs,NATIVE_ACTIONS


@lru_cache(maxsize=1)
def labels():
    data=yaml.safe_load(Path(__file__).with_name('action_catalog.yaml').read_text(encoding='utf8'))
    result={verb:row.get('kinds',[{}])[0].get('label',verb) for verb,row in data.get('verbs',{}).items()}
    result.update(buy='購入する',concede='交換条件に合意する',confront='証拠を突きつける',craft='品物を作る',disguise='変装する',donate='寄付する',fight='戦う',give_item='品物を渡す',grand_gesture='贈り物で気持ちを伝える',guard='品物を守る',investigate='調査する',labor='働いて入手する',mislead='相手を惑わせる',move='移動する',negotiate='交渉する',neutralize='効果を無力化する',observe='観察する',payoff='伏線を回収する',persuade='説得する',plant='伏線を仕込む',pledge='誓約する',rescue='救助する',rest='休息する',rethink='考え直す',sabotage='妨害する',sacrifice='犠牲を払う',share_knowledge='情報を共有する',train='訓練する',trial='試練に挑む',withdraw='身を引く')
    result.update(work='作業する',wait='待機する',offer='作業を提案する',change='作業の提案を変更する',accept='作業の提案を受け入れる',accept_change='作業の変更を受け入れる',cancel='作業を取り消す',deliver='成果を届ける',tell='出来事を伝える',depend='作業を依頼する',replan='作業計画を変更する',practice='練習する',costume_prepare='衣装を準備する',sound_check='音響を確認する',exhibit_help='展示を手伝う',dialogue='対話する',join_activity='共同活動に参加する',decline_activity='共同活動を辞退する',leave_activity='共同活動から離れる',await_activity='共同活動を待つ',joint_move='共同で移動する',joint_work='共同で作業する')
    return result


def catalog(definition):
    standard=float((definition.get('stamina') or {}).get('default_action_cost',1))
    dynamic={'move':'移動経路ごとの消費量','labor':'入手経路ごとの消費量（未設定時は通常の消費量）','respond':'作業ごとの消費量','work':'作業時間・作業ルールごとの消費量','joint_move':'参加する役割・移動時間ごとの消費量','joint_work':'参加する役割・作業時間ごとの消費量'}
    rows=[]
    for verb in sorted(action_verbs(),key=lambda v:labels().get(v,v)):
        base=0 if verb in {'rest','rescue'} or verb in NATIVE_ACTIONS else 1+standard if verb=='train' else standard
        rows.append(dict(id=verb,name=labels().get(verb,verb),base=None if verb in dynamic else base,defaultDescription=dynamic.get(verb,str(base).removesuffix('.0')),native=verb in NATIVE_ACTIONS))
    return rows
