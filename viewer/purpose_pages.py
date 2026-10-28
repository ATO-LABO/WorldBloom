"""Purpose selection and saved independent-trial review; no simulation logic."""
from __future__ import annotations
from viewer import pages


def context(*, choosing=False):
    value = '<span>未選択</span>' if choosing else '<span data-purpose-label>物語制作</span>'
    return ('<div class="purpose-context" aria-label="目的"><span class="purpose-context-label">目的</span>'
            + value + '</div>' +
            '<span class="purpose-top-status" data-purpose-notice role="status"></span>')


def choices(destination="/worlds", heading="h2"):
    return ('<section class="purpose-choices" aria-label="目的を選ぶ">'
            f'<div><{heading}>今日は何を進めますか</{heading}><p>目的を選んでから、使う世界を選びます。</p></div>'
            f'<a href="{destination}?mode=create" data-purpose-link="create"><strong>物語制作</strong>'
            '<span>候補を探し、特徴を比べ、物語を選ぶ</span></a>'
            f'<a href="{destination}?mode=review" data-purpose-link="review"><strong>既存物語レビュー</strong>'
            '<span>同じ世界と方針で試し、結末の内訳と分岐を確かめる</span></a></section>')


def landing(job_store=None):
    return pages.document('目的を選ぶ', choices('/worlds', 'h1'), job_store=job_store,
                          show_phase_band=False, is_home=True, page_class='purpose-landing', choosing_purpose=True)


def independent_controls(job_store):
    if job_store is None:
        return '<p>新規レビュー実行は未接続です。この画面で実行やシミュレーションの取消はできません。</p>'
    return ('<section data-independent-root><h3>独立CPU試行を実行</h3>'
            '<p>保存済み設定の実行契約と事前seedで、選別・原作誘導なしの独立試行を実行します。通常の設定はgenome・外部judgeなしです。</p>'
            '<p>承認済みMelosでは通常9遺伝子の固定中立設定を明示的に選べます。物語側の完了と暦の観測終了、王城到着・2分の報告・120分の婚礼を分けて記録します。</p>'
            '<button type="button" data-independent-melos-profile>Melos 通常9遺伝子の設定を保存・選択</button>'
            '<label>保存済み設定<select data-independent-config aria-label="保存済み設定"></select></label>'
            '<label>事前seed（1〜100件）<input data-independent-seeds value="0" maxlength="1200" inputmode="numeric"></label>'
            '<button type="button" data-independent-start>独立CPU試行を開始</button>'
            '<button type="button" data-independent-retry disabled>同じ要求・ジョブを確認</button>'
            '<button type="button" data-independent-cancel disabled>ジョブの取消を要求</button>'
            '<p data-independent-status role="status" aria-live="polite">利用できる設定を確認しています。</p>'
            '<details><summary>今回の確定要求</summary><pre data-independent-plan class="purpose-story"></pre></details>'
            '<p>画面を離れても実行は続きます。取消要求と、所有プロセスの終了確認は別の状態です。過去の結果は入力変更で書き換わりません。</p></section>')


def render(repository, *, job_store=None):
    worlds = pages._library_worlds(job_store)
    options = ''.join(f'<option value="{pages._escape(w["id"])}">{pages._escape(w.get("name") or w["id"])}</option>' for w in worlds)
    create_action = ('<a class="button" data-purpose-run href="/jobs">候補探索の実行画面へ</a>'
                     if job_store else '<p>閲覧専用です。候補探索の実行はStudioで利用できます。</p>')
    body = (
        '<div class="purpose-shell" data-purpose-root>'
        '<section class="purpose-common"><h2>共通の入力</h2>'
        '<label>世界<select data-purpose-world><option value="">世界を選ぶ…</option>' + options + '</select></label>'
        '<label>目的・注目したい点<textarea data-purpose-note maxlength="3000" rows="3" placeholder="この作業で確かめたいこと"></textarea></label>'
        '<p>世界の選択とメモは、この作業の入力として保持します。メモは実行設定を変更しません。</p></section>'
        '<section data-purpose-panel="create"><h2>物語制作</h2>'
        '<p>GAなどで候補を探し、特徴を比べて採用する物語を選びます。候補の採用数は独立試行の結末分布には使いません。</p>'
        + create_action + '<a class="button" href="/selected">候補の吟味・比較へ</a>'
        '<p>実行中の進捗・取消は既存の実行画面で確認できます。</p></section>'
        '<section data-purpose-panel="review" hidden><h2>既存物語レビュー</h2>'
        '<p>世界と方針を固定した独立試行から、結末の件数と分岐の根拠を読みます。原作への一致を得点化して選別した結果は受け付けません。</p>'
        '<div class="purpose-availability" role="status"><strong>保存済み結果を読めます</strong>'
        + independent_controls(job_store) + '</div>'
        '<label class="purpose-file">レビュー結果／共通実行記録JSONを選ぶ<input type="file" data-purpose-file accept=".json,application/json"></label>'
        '<button type="button" data-purpose-import>保存済み結果を読み込む</button>'
        '<button type="button" data-purpose-cancel disabled>読込を取消</button>'
        '<p>worldbloom_review_v1の独立試行集計、またはschema_version 1のRunRecordを読めます。最大16MiB。ファイルはこのブラウザ内で読み、サーバーへ送信しません。</p>'
        '<p>共通実行記録は単回結果として表示し、目標結末・実際の終端・停止理由・観測位置を分けます。候補探索・独立試行・明示行動・再生の実行種別は保存されたExecutionPlanから読みます。</p>'
        '<p>表示する世界・方針・seedは結果の実行時の版です。共通入力の変更で過去結果を書き換えません。</p>'
        '<p data-purpose-status role="status" aria-live="polite">結果を読み込むと結末の内訳が表示されます。</p>'
        '<label>過去の結果<select data-purpose-history aria-label="過去の結果"></select></label>'
        '<button type="button" data-purpose-copy disabled>選択中の記録JSONをコピー</button>'
        '<p data-purpose-copy-status role="status" aria-live="polite"></p>'
        '<textarea data-purpose-copy-fallback aria-label="コピーする保存記録JSON" rows="5" readonly hidden></textarea>'
        '<div data-purpose-result></div></section>'
        '<p class="purpose-storage" data-purpose-storage role="status"></p>'
        '<a class="button" href="/">世界の一覧へ戻る</a>'
        '<noscript><p>目的の表示と結果読込にはJavaScriptが必要です。既存の世界一覧と実行画面はリンクから開けます。</p></noscript></div>')
    html=pages.document('目的の作業画面', body, job_store=job_store, show_phase_band=False, stylesheets=(), scripts=('purpose-independent.js',) if job_store is not None else ())
    return html
