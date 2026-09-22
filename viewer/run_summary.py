"""Shared exploration summary for editable settings and saved conditions."""
from viewer.pages import _escape as E


def render(evolution, *, preview=None, editable=False, estimate_html=None):
    """estimate_html is trusted markup produced by workbench_pages._estimate."""
    g, p, s = (evolution[key] for key in ("generations", "population", "seeds"))
    factor = 2 if evolution.get("coevolve") else 1
    preview = preview or {}
    individuals = preview.get("planned_individual_evaluations", g * p * factor)
    evaluations = preview.get("planned_seed_evaluations", g * p * s * factor)
    equation = f"{g}世代 × {p}個体 × {s}回" + (" × 2陣営" if factor == 2 else "")
    note = "設定を変えると、ここも更新されます。" if editable else "保存した条件で探索します。"
    estimate = estimate_html if estimate_html and estimate_html != "—" else (
        "実行条件で確認できます" if editable else "まだ見積もりの記録がありません")
    return (
        '<aside class="rs-summary" data-run-summary aria-label="今回の探索">'
        '<h2>今回の探索</h2>' + f'<p>{note}</p>'
        f'<strong class="rs-equation" data-scale-equation>{E(equation)}</strong>'
        '<dl><dt>評価する個体</dt>'
        f'<dd><b data-individual-total>{individuals:,}</b> 個体</dd>'
        '<dt>シミュレーション</dt>'
        f'<dd><b data-total>{evaluations:,}</b> 回</dd></dl>'
        '<div class="rs-estimate"><span>所要時間の目安</span>'
        f'<p>{estimate}</p></div>'
        '<h3>実行後の流れ</h3><ol><li>実行状況で進み具合を確認</li>'
        '<li>Siftingで候補を選ぶ</li><li>選んだ候補から文章を生成</li></ol>'
        '<p class="rs-llm-note">この探索ではLLMを使用しません。</p></aside>'
    )
