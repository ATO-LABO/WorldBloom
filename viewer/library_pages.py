"""World and genre library: HTML pages and JSON APIs (WB-UI-010 Stage 2).

Every persistent change goes through execution.library.LibraryStore; this
module only renders HTML, parses requests and enforces the POST boundary
(job_api.boundary). Screen rendering never calls an LLM. Semantic validation
of a world/genre pair is never reimplemented here -- /api/*/validate calls
straight through to execution.configs.ConfigStore.preview via LibraryStore.
"""
from __future__ import annotations

import hashlib
from http import HTTPStatus

import yaml

from execution.library import LibraryStore
from execution.provenance import ConfigError
from execution.worker import TERMINAL
from execution.world_patch_approval import (StalePatch as _StalePatch, approve as _approve_patch,
                                            reject as _reject_patch, reopen as _reopen_patch,
                                            retire as _retire_patch)
from execution.world_patch_library import (DuplicateAssetError as _DuplicateAssetError,
                                           StaleImport as _StaleImport, export_patch as _export_patch,
                                           import_patch as _import_patch)
from gapengine.world_patch import (ID_RE as _PATCH_ID_RE, PatchError as _WorldPatchError,
                                  approved_patches as _approved_patches, stack_head as _patch_stack_head)
from gapengine.world_patch_usage import patch_usage as _patch_usage
from viewer import action_catalog, data, pages, job_api, world_demand_view, world_expansion_view, world_graph
from viewer.workbench_pages import _guidance_page, _job_store, _query

_escape = pages._escape
_url = pages._url_segment

GENRE_FILE_LABELS = {
    "action_graph.yaml": "行動グラフ", "canon.yaml": "正典プライア", "effects.yaml": "効果表",
    "qd.yaml": "QD軸", "rules.yaml": "ルール",
    "action_graph.antagonist.yaml": "行動グラフ（敵役）", "canon.antagonist.yaml": "正典プライア（敵役）",
}

NOTE = ('<p class="library-note">保存しても過去の実行設定と実験結果は変わりません'
        '（実行時に写しを取ります）。</p>')


# --------------------------------------------------------------------------
# Small render helpers
# --------------------------------------------------------------------------

def _editor_block(rel, content, *, label=None, subject_id=None):
    heading = f"{label}（{rel}）" if label else rel
    subject_attr = f' data-subject-id="{_escape(subject_id)}"' if subject_id is not None else ""
    return (
        f'<details open class="editor"><summary>{_escape(heading)}</summary>'
        f'<textarea data-file="{_escape(rel)}"{subject_attr} aria-label="{_escape(heading)}" rows="16" spellcheck="false">{_escape(content)}</textarea>'
        f'<button type="button" data-action="save-file" data-path="{_escape(rel)}">保存</button>'
        f'<span data-save-status data-for="{_escape(rel)}"></span>'
        f'<span class="field-error" data-error-for="{_escape(rel)}" role="alert"></span>'
        "</details>"
    )


_NEW_WORLD_CARD = (
    '<a class="world-card world-card--new" href="/worlds/new">'
    '<span class="world-card-new-plus" aria-hidden="true">+</span>新しい世界を作る</a>'
)


def _world_card(world):
    genre = world["genre"]
    edit_href = f"/worlds/{_url(world['id'])}"
    run_href = (
        f"/configs/new?project={_url(world['id'])}&template={_url(genre)}"
        if genre else f"/configs/new?project={_url(world['id'])}"
    )
    return (
        '<article class="world-card">'
        f'<a class="world-card-link" href="{edit_href}">'
        f'<h3>{_escape(world["name"] or world["id"])}</h3>'
        "<dl>"
        f'<div><dt>ID</dt><dd>{_escape(world["id"])}</dd></div>'
        f'<div><dt title="{_escape(pages.TERM_HELP["genre"])}">ジャンル</dt>'
        f'<dd>{_escape(genre) if genre else "—"}</dd></div>'
        f'<div><dt>人物数</dt><dd>{_escape(world["subjects"])}</dd></div>'
        f'<div><dt>主人公/敵役</dt><dd>{_escape(world["protagonist"])} / {_escape(world["antagonist"])}</dd></div>'
        "</dl></a>"
        '<div class="world-card-footer">'
        + pages.quick_start_actions(world["id"], genre, world["name"] or world["id"], run_href, css_class="button", text="すぐ実行")
        + "</div></article>"
    )


def _genre_row(genre):
    used = ", ".join(genre["used_by"]) if genre["used_by"] else "—"
    return (
        "<tr>"
        f'<td><a href="/genres/{_url(genre["id"])}">{_escape(genre.get("name") or genre["id"])}</a></td>'
        f'<td>{_escape(", ".join(genre["files"]))}</td>'
        f'<td>{_escape(used)}</td>'
        f'<td class="wb-actions"><a href="/genres/{_url(genre["id"])}">編集</a></td>'
        "</tr>"
    )


def render_worlds_hub(worlds, *, can_create, heading=True):
    cards = (_NEW_WORLD_CARD if can_create else "") + "".join(_world_card(w) for w in worlds)
    body = f'<div class="world-hub-grid">{cards}</div>' if cards else "<p>世界がありません。</p>"
    head = "<h2>世界</h2>" if heading else ""
    return f'<section class="card">{head}' + body + "</section>"


GENRE_LEAD = (
    '<p class="muted">ジャンルは行動の文法（行動グラフ・正典プライア・効果表・ルール・QD軸）。'
    "templates/&lt;ジャンル&gt; に置かれ、世界から差し込んで使います。"
    "同じジャンルを複数の世界で使うと、人物や場所が違っても展開のルールは共通になります。</p>"
)


def render_genres_section(genres, *, heading=True):
    # Genres are grammar shared across worlds: this section also appears on
    # the ⚙ 設定 page (/configs) and, since WB-UI-023, on the home hub's
    # ジャンル tab (rendered without its own heading there).
    genre_table = (
        '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
        "<th>ID</th><th>ファイル</th><th>使っている世界</th><th>操作</th>"
        f'</tr></thead><tbody>{"".join(_genre_row(g) for g in genres)}</tbody></table></div>'
    ) if genres else "<p>ジャンルがありません。</p>"
    head = "<h2>ジャンル</h2>" if heading else ""
    return (
        f'<section class="card" id="genres">{head}{GENRE_LEAD}' + genre_table
        + '<p><a href="/genres/new">新しいジャンルを作る</a></p></section>'
    )


def render_home_tabs(worlds, genres, *, can_create):
    """WB-UI-023: home hub as 世界/ジャンル tabs, so a visitor can see the

    engine's genre layer without leaving the front page (it was previously
    reachable only from /configs). 世界 stays the default tab.
    """
    panels = [
        ("世界", render_worlds_hub(worlds, can_create=can_create, heading=False)),
        (f"ジャンル（{len(genres)}）", render_genres_section(genres, heading=False)),
    ]
    return pages.tabs("home", panels)


def render_world_new_form(worlds, genres, *, from_id, genre_id):
    world_options = "".join(
        f'<option value="{_escape(w["id"])}"{" selected" if w["id"] == from_id else ""}>'
        f'{_escape(w["name"] or w["id"])}</option>'
        for w in worlds
    )
    genre_options = "".join(
        f'<option value="{_escape(g["id"])}"{" selected" if g["id"] == genre_id else ""}>{_escape(g.get("name") or g["id"])}</option>'
        for g in genres
    )
    return (
        '<form data-wb="library-create" data-kind="world">'
        '<p class="form-error" data-form-error role="alert"></p>'
        '<div class="form-grid">'
        '<div class="field"><label for="f-world_id">新しいID</label>'
        '<input id="f-world_id" type="text" name="world_id" data-field="world_id" required>'
        '<span class="field-error" data-error-for="world_id" role="alert"></span></div>'
        '<div class="field"><label for="f-name">表示名</label>'
        '<input id="f-name" type="text" name="name" data-field="name" required>'
        '<span class="field-error" data-error-for="name" role="alert"></span></div>'
        '<div class="field"><label for="f-from">複製元の世界</label>'
        f'<select id="f-from" name="from_world_id" data-field="from_world_id">{world_options}</select>'
        '<span class="field-error" data-error-for="from_world_id" role="alert"></span></div>'
        '<div class="field"><label for="f-genre">ジャンル</label>'
        f'<select id="f-genre" name="template_id" data-field="template_id">{genre_options}</select>'
        '<span class="field-error" data-error-for="template_id" role="alert"></span></div>'
        "</div>"
        '<button type="submit">作成</button>'
        "</form>"
    )


def render_genre_new_form(genres, *, from_id):
    genre_options = "".join(
        f'<option value="{_escape(g["id"])}"{" selected" if g["id"] == from_id else ""}>{_escape(g.get("name") or g["id"])}</option>'
        for g in genres
    )
    return (
        '<form data-wb="library-create" data-kind="genre">'
        '<p class="form-error" data-form-error role="alert"></p>'
        '<div class="form-grid">'
        '<div class="field"><label for="f-template_id">新しいID</label>'
        '<input id="f-template_id" type="text" name="template_id" data-field="template_id" required>'
        '<span class="field-error" data-error-for="template_id" role="alert"></span></div>'
        '<div class="field"><label for="f-from">複製元のジャンル</label>'
        f'<select id="f-from" name="from_template_id" data-field="from_template_id">{genre_options}</select>'
        '<span class="field-error" data-error-for="from_template_id" role="alert"></span></div>'
        "</div>"
        '<button type="submit">作成</button>'
        "</form>"
    )


def _world_yaml_mapping(repo, world_id):
    path = repo / "projects" / world_id / "world.yaml"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _genre_yaml(repo, genre_id, filename):
    if not genre_id:
        return None
    path = repo / "templates" / genre_id / filename
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _overview_panel(world, world_yaml, subjects):
    protagonist_id = world["protagonist"]
    antagonist_id = world["antagonist"]
    protagonist_subject = next((s for s in subjects if str(s.get("id")) == protagonist_id), None)
    goal = (protagonist_subject or {}).get("goal") or {}
    target = goal.get("target") if isinstance(goal, dict) else None
    deliver_to = goal.get("deliver_to") if isinstance(goal, dict) else None
    protagonist_label = _escape(protagonist_id) if protagonist_id else "主人公"

    if target:
        intro = f"{protagonist_label}が「{_escape(target)}」を求める物語"
        if deliver_to:
            intro += f"（届け先: {_escape(deliver_to)}）"
        intro += "。"
    else:
        intro = f"{protagonist_label}の物語。"
    if antagonist_id:
        intro += f" 立ちはだかるのは{_escape(antagonist_id)}。"

    time_info = world_yaml.get("time") or {}
    days = time_info.get("days")
    day_text = _escape(days) if days else "—"
    zones = world_yaml.get("zones") or []
    intro += f" {day_text}日間・{len(zones)}か所・{_escape(world['subjects'])}人。"

    if "overview" in world_yaml:
        intro = _escape(world_yaml.get("overview") or "世界の概要はまだ設定されていません。")

    genre = world["genre"]
    genre_html = f'<a href="/genres/{_url(genre)}">{_escape(genre)}</a>' if genre else "—"
    characters_html = (
        f'{_escape(world["subjects"])}人（主人公: {_escape(protagonist_id or "未設定")} '
        f'／ 敵役: {_escape(antagonist_id or "未設定")}）'
    )
    zone_names = "・".join(
        _escape(zone["name"]) for zone in zones if isinstance(zone, dict) and zone.get("name")
    )
    places_html = f"{len(zones)}か所: {zone_names}" if zone_names else f"{len(zones)}か所"
    slots = time_info.get("slots") or []
    period_html = day_text + "日間" + (f"・{'・'.join(_escape(s) for s in slots)}" if slots else "")

    facts = (
        '<dl class="world-summary world-facts">'
        f"<dt>ジャンル</dt><dd>{genre_html}</dd>"
        f"<dt>登場人物</dt><dd>{characters_html}</dd>"
        f"<dt>場所</dt><dd>{places_html}</dd>"
        f"<dt>期間</dt><dd>{period_html}</dd>"
        "</dl>"
    )
    goals = world_graph.world_summary(
        subjects, protagonist=protagonist_id, antagonist=antagonist_id,
    )
    return (
        f'<p class="world-intro">{intro}</p>'
        + facts
        + "<h3>目的</h3>" + goals
        + '<p class="muted edit-hint"><a href="#world-files">world.yaml を直接編集</a>（名前・主人公・敵役）</p>'
    )


def _characters_panel(world, world_yaml, subjects, store, editors_html):
    svg = world_graph.relation_svg(subjects, protagonist=world["protagonist"], antagonist=world["antagonist"])
    table = world_graph.character_table(subjects, protagonist=world["protagonist"], antagonist=world["antagonist"])
    effects = _genre_yaml(store.repo, world.get("genre"), "effects.yaml")
    readout_html = world_graph.character_readout_html(
        world_yaml, subjects, effects if isinstance(effects, list) else [],
        protagonist=world["protagonist"], antagonist=world["antagonist"],
    )
    people, articles = [], []
    for index, subject in enumerate(subjects):
        name = str(subject.get("id") or "")
        if not name:
            continue
        role = "主人公" if name == world["protagonist"] else "敵役" if name == world["antagonist"] else "登場人物"
        goal = subject.get("goal") or {}
        goal = goal if isinstance(goal, dict) else {}
        target = goal.get("target")
        purpose = f'「{_escape(target)}」を求めている。' if target else "目的は設定されていません。"
        if goal.get("deliver_to"):
            purpose += f' 届け先は{_escape(goal["deliver_to"])}。'
        identity = subject.get("identity") or {}
        identity = identity if isinstance(identity, dict) else {}
        identity_text = identity.get("true", identity.get(True)) or identity.get("displayed")
        intro = f'<p>{_escape(identity_text)}</p>' if identity_text else ""
        inventory = subject.get("inventory") or {}
        inventory_text = "、".join(f'{_escape(k)} × {_escape(v)}' for k, v in inventory.items()) if isinstance(inventory, dict) else ""
        knowledge = subject.get("knowledge") or []
        knowledge_text = "、".join(_escape(item) for item in knowledge) if isinstance(knowledge, list) else ""
        people.append(
            f'<a class="world-person-link" href="#world-person-{index}" data-world-person="{index}">'
            f'<strong>{_escape(name)}</strong><span>{role}</span></a>'
        )
        edit = (f'<button type="button" class="button" data-world-edit-subject="{_escape(name)}">この人物を編集</button>'
                if editors_html else "")
        articles.append(
            f'<article class="world-person" id="world-person-{index}" data-person-panel="{index}" tabindex="-1">'
            f'<p class="world-eyebrow">{role}</p><h3>{_escape(name)}</h3>{intro}'
            f'<h4>この人物が求めるもの</h4><p>{purpose}</p>'
            f'<h4>持ち物と知識</h4><p>{inventory_text or "持ち物は設定されていません。"}</p>'
            f'<p>{knowledge_text or "知識は設定されていません。"}</p>'
            '<div class="world-person-actions">' + edit
            + f'<button type="button" class="button" data-world-sheet="sheet-{index}">能力・秘密・伏線を読む</button></div>'
            '</article>'
        )
    return (
        '<div class="world-people"><nav class="world-people-list" aria-label="登場人物一覧">'
        + ("".join(people) or '<p>人物がいません。</p>') + '</nav>'
        + '<div class="world-people-detail">' + "".join(articles)
        + '<details class="world-support"><summary>人物の相関図と比較表</summary>' + svg
        + '<p>線の色: 緑=好意 / 赤=敵意 / 灰=中立。太さ=好感度の強さ、濃さ=認知度。'
        '線にカーソルを合わせると双方向の値が出ます。</p>' + table + '</details>'
        + editors_html + '</div></div>' + readout_html
    )


def _canon_panel(world, subjects, store):
    # WB-UI-020: the genre's canon, phrased for the reader. The objective
    # item is named after the protagonist's goal so "宝を敵が持っている"
    # reads as this world's story rather than as a context key.
    canon = _genre_yaml(store.repo, world.get("genre"), "canon.yaml")
    protagonist = next((s for s in subjects if str(s.get("id")) == world["protagonist"]), None)
    goal = (protagonist or {}).get("goal") or {}
    objective = str(goal.get("target") or "目的の品") if isinstance(goal, dict) else "目的の品"
    canon_html = (
        world_graph.canon_table_html(canon, objective=objective) if isinstance(canon, dict)
        else '<p class="muted">このジャンルに正典データがありません。</p>'
    )
    genre = world.get("genre")
    hint = (
        f'<p class="muted edit-hint">定石はジャンル側の設定です → '
        f'<a href="/genres/{_url(genre)}">ジャンル「{_escape(genre)}」を編集</a></p>'
    ) if genre else ""
    return (
        "<h3>状況別の定石 早見表（GA はここから離れる）</h3>"
        + '<p class="muted">上から順に読む筋書きではありません。GA 実行中の各瞬間に、'
        "左の条件がそろった行が参照される早見表です（同じジャンルの世界ならどれも同じ内容）。"
        "ジャンルの canon.yaml に手書きされた「この状況ならふつうこうする」の一覧で、"
        "GA の主人公は遺伝子 novelty_drive が高いほど、右の行動を選びにくくなります。"
        "定石の強さは相対的な重みで、いちばん大きい行をいっぱいとして描きます。</p>"
        + canon_html
        + hint
    )


def _places_panel(world_yaml):
    zones = world_yaml.get("zones") or []
    routes = world_yaml.get("routes") or {}
    return (
        world_graph.zone_list_html(zones) + world_graph.zone_svg(zones, routes)
        + '<p class="muted edit-hint"><a href="#world-files">world.yaml を直接編集</a>（zones ／ routes）</p>'
    )


def _period_panel(world_yaml):
    time_info = world_yaml.get("time") or {}
    days = time_info.get("days")
    slots = time_info.get("slots") or []
    day_count = _escape(days) if days else "—"
    return (
        "<h3>1日の時間帯</h3>" + world_graph.day_cycle_svg(slots)
        + f"<h3>日程（{day_count}日間）</h3>" + world_graph.calendar_grid_html(days)
        + '<p class="muted edit-hint"><a href="#world-files">world.yaml を直接編集</a>（time）</p>'
    )


def _editor_group(store, world, job_store):
    if job_store is None:
        return '<p class="library-note">閲覧専用です。編集・検証はWorldBloom Studioで利用できます。</p>'
    world_id = world["id"]
    other_files = [rel for rel in store.world_files(world_id) if not rel.startswith("subjects/")]
    contents = {rel: store.read("world", world_id, rel) for rel in other_files}
    genre_options = "".join(
        f'<option value="{_escape(g["id"])}"{" selected" if g["id"] == world["genre"] else ""}>{_escape(g.get("name") or g["id"])}</option>'
        for g in store.genres()
    )
    validate_form = (
        NOTE
        + '<form data-action="validate">'
        f'<label>検証するジャンル <select data-field="other_id">{genre_options}</select></label> '
        '<button type="submit">検証</button>'
        '<div data-field="validation" class="validation"></div>'
        "</form>"
    )
    editors = "".join(_editor_block(rel, contents[rel]) for rel in other_files)
    return (
        '<details class="editor-group">'
        "<summary>world.yaml を直接編集</summary>"
        + validate_form
        + f'<section class="card" id="world-files"><h2>ファイル</h2>{editors}</section>'
        + "</details>"
    )


def _subject_editors_html(store, world, job_store):
    if job_store is None:
        return ""
    world_id = world["id"]
    subject_files = [rel for rel in store.world_files(world_id) if rel.startswith("subjects/")]
    contents = {rel: store.read("world", world_id, rel) for rel in subject_files}
    template_content = contents[subject_files[0]] if subject_files else yaml.safe_dump({
        "id": "新しい人物", "traits": {"social": 0.5, "stubbornness": 0.5, "curiosity": 0.5, "diligence": 0.5, "temper": 0.5},
        "base": 50, "range": {"entry": "", "zones": []}, "goal": {},
    }, allow_unicode=True, sort_keys=False)
    editors = []
    for rel in subject_files:
        try:
            subject = yaml.safe_load(contents[rel])
        except yaml.YAMLError:
            subject = None
        subject_id = subject.get("id") if isinstance(subject, dict) else None
        editors.append(_editor_block(rel, contents[rel], subject_id=subject_id))
    editors = "".join(editors)
    add_subject = (
        '<section class="card"><h2>人物を追加</h2>'
        '<div class="field"><label for="f-subject-name">ファイル名（subjects/ 以下、拡張子なし）</label>'
        '<input id="f-subject-name" type="text" data-subject-name placeholder="08_new"></div>'
        f'<textarea data-subject-content rows="16" spellcheck="false">{_escape(template_content)}</textarea>'
        '<button type="button" data-action="add-subject">追加保存</button>'
        '<span data-save-status data-for="subjects-new"></span>'
        '<span class="field-error" data-error-for="subjects-new" role="alert"></span>'
        "</section>"
    )
    return (
        '<details class="editor-group" id="subject-files">'
        "<summary>人物ファイルを編集（subjects/…）</summary>"
        + editors + add_subject + "</details>"
    )


def render_world_detail(world, store, job_store):
    run_href = (
        f"/configs/new?project={_url(world['id'])}&template={_url(world['genre'])}"
        if world["genre"] else f"/configs/new?project={_url(world['id'])}"
    )
    world_yaml = _world_yaml_mapping(store.repo, world["id"])
    subjects = world_graph.load_subjects(store.repo / "projects" / world["id"])
    world_id = world["id"]
    editors_html = _subject_editors_html(store, world, job_store)
    from viewer.world_create import basics_editor
    overview = _overview_panel(world, world_yaml, subjects)
    if job_store is not None:
        overview += basics_editor(world_id, world_yaml)
    if "initial_story" in world_yaml:
        initial_story = '<h3>シミュレーション開始時点の導入・状況</h3><p class="wc-copy">'+_escape(world_yaml.get("initial_story") or "初期物語はまだ設定されていません。")+'</p>'
        if job_store is not None:
            initial_story += basics_editor(world_id, world_yaml, story=True)
        initial_story += _canon_panel(world, subjects, store)
    else:
        initial_story = _canon_panel(world, subjects, store)
    panels = [
        ("概要", overview),
        ("登場人物", _characters_panel(world, world_yaml, subjects, store, editors_html)),
        ("初期物語", initial_story),
        ("行動図鑑", action_catalog.catalog_panel_html(world, store.repo)),
        ("場所", _places_panel(world_yaml)),
        ("期間", _period_panel(world_yaml)),
    ]
    panels = [(label, f'<h2 class="visually-hidden">{label}</h2>' + content) for label, content in panels]
    card = f'<section class="world-detail">{pages.tabs("world", panels)}</section>'
    files_block = _editor_group(store, world, job_store)
    cta = pages.quick_start_actions(
        world["id"], world["genre"], world["name"] or world["id"], run_href,
        css_class="button primary", text="この世界で実験を回す", repo=store.repo,
    )
    missing = []
    if not subjects: missing.append("登場人物")
    if not world_yaml.get("zones"): missing.append("場所")
    people_ids = {str(person.get("id")) for person in subjects}
    if not world.get("protagonist") or world.get("protagonist") not in people_ids: missing.append("主人公")
    if not world.get("antagonist") or world.get("antagonist") not in people_ids: missing.append("敵役")
    if not world_yaml.get("target_ending"): missing.append("目標の結末")
    cta_html = (f'<p class="actions world-cta"><span>この世界は設定途中です。{"・".join(missing)}を設定してから、実行に進めます。</span></p>'
                if missing else f'<p class="actions world-cta"><span>世界を確かめたら、実験へ。</span>{cta}</p>')
    if job_store is None:
        return ('<div class="world-workspace">' + card + files_block + '</div>'
                + '<p class="actions world-cta">実験を始めるにはWorldBloom Studioでこの世界を開いてください。</p>')
    return (
        f'<div class="world-workspace" data-wb="library" data-kind="world" data-owner="{_escape(world_id)}">'
        + card + files_block + "</div>" + cta_html
    )


def render_genre_detail(genre, contents, worlds):
    world_options = "".join(
        f'<option value="{_escape(w["id"])}">{_escape(w["name"] or w["id"])}</option>' for w in worlds
    )
    used = ", ".join(genre["used_by"]) if genre["used_by"] else "—"
    header = (
        '<section class="card">'
        f'<p>使っている世界: {_escape(used)}</p>'
        + NOTE
        + '<form data-action="validate">'
        f'<label>検証する世界 <select data-field="other_id">{world_options}</select></label> '
        '<button type="submit">検証</button>'
        '<div data-field="validation" class="validation"></div>'
        "</form></section>"
    )
    editors = "".join(
        _editor_block(rel, contents[rel], label=GENRE_FILE_LABELS.get(rel))
        for rel in genre["files"]
    )
    return (
        f'<div data-wb="library" data-kind="genre" data-owner="{_escape(genre["id"])}">'
        + header + f'<section class="card"><h2>ファイル</h2>{editors}</section>'
        + "</div>"
    )


# --------------------------------------------------------------------------
# GET route handlers
# --------------------------------------------------------------------------

def _worlds_list(handler):
    job_store = _job_store(handler)
    handler._send_html(pages.index_page(handler.repository, job_store=job_store))


def _worlds_new(handler):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    store = LibraryStore(job_store.configs.repo)
    worlds, genres = store.worlds(), store.genres()
    world_ids = {w["id"] for w in worlds}
    genre_ids = {g["id"] for g in genres}
    query = _query(handler)
    from_id = query.get("from", [None])[0]
    if from_id not in world_ids:
        from_id = worlds[0]["id"] if worlds else None
    genre_id = query.get("genre", [None])[0]
    if genre_id not in genre_ids:
        source = next((w for w in worlds if w["id"] == from_id), None)
        genre_id = source["genre"] if source and source["genre"] in genre_ids else None
    from viewer import world_create
    handler._send_html(world_create.render(store, job_store, from_id=from_id,
        genre_id=genre_id, copy_mode="from" in query))


def _world_experiments(repository, world_name):
    groups, minor = data.grouped_experiments(repository)
    return [str(m["name"]) for m in dict(groups).get(world_name, []) + [m for m in minor if str(m["world"]) == world_name]]


def _world_expansion_html(store, world_id, world_yaml, job_store):
    """「後から生まれたもの」節。壊れていても世界画面は落とさない
    （WB-WORLDGROW-001 段階3b-1）。"""
    try:
        state = world_expansion_view.load(store.repo / "projects" / world_id)
        return world_expansion_view.approved_list(
            state, world_id=world_id, can_write=job_store is not None, world=world_yaml,
            run_link=lambda name: f"/exp/{_url(name)}/monitor?tab=demand",
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        return ""


def _worlds_detail(handler, world_id):
    job_store = _job_store(handler)
    repo = job_store.configs.repo if job_store is not None else data.ROOT
    store = LibraryStore(repo)
    world = next((w for w in store.worlds() if w["id"] == world_id), None)
    if world is None:
        raise ConfigError("world_id", "世界がありません", code="not_found")
    label = world["name"] or world_id
    if _query(handler).get("view") != ["advanced"]:
        from execution.world_editor import snapshot
        from viewer import world_prototype
        current = snapshot(store, world_id)
        handler._send_html(world_prototype.render(
            world, current["world"], current["people"], revision=current["revision"],
            job_store=job_store, pin=data.pinned_target(job_store),
            # Read-only viewers have no run screens; the world page is their way in to saved experiments.
            experiments=[] if job_store is not None else _world_experiments(handler.repository, label),
            expansion_html=_world_expansion_html(store, world_id, current["world"], job_store),
        ))
        return
    from viewer import world_advanced
    handler._send_html(world_advanced.render(world, store, job_store))



def _genres_new(handler):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    from viewer import genre_pages
    genre_pages.new(handler)


def _genres_detail(handler, genre_id):
    if _job_store(handler) is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    from viewer import genre_pages
    genre_pages.detail(handler, genre_id)


def _genre_editor_action(handler, genre_id, operation):
    _require_job_store(handler)
    body = _boundary_body(handler)
    from viewer import genre_pages
    genre_pages.action(handler, genre_id, operation, body)


# --------------------------------------------------------------------------
# POST API handlers
# --------------------------------------------------------------------------

def _require_job_store(handler):
    job_store = _job_store(handler)
    if job_store is None:
        raise ConfigError("service", "実行管理は未設定です", code="unavailable")
    return job_store


def _boundary_body(handler):
    handler.connection.settimeout(5)
    try:
        body = handler._request_json()
    except ValueError as error:
        raise ConfigError("request", "JSON本文が不正です", code="bad_request") from error
    job_api.boundary(handler)
    return body


def _file_body(body):
    path, content = body.get("path"), body.get("content")
    if not isinstance(path, str) or not path:
        raise ConfigError("path", "対象ファイルを指定してください", code="bad_request")
    if not isinstance(content, str):
        raise ConfigError("content", "文字列を指定してください", code="bad_request")
    return path, content


def _create_world(handler):
    job_store = _require_job_store(handler)
    body = _boundary_body(handler)
    store = LibraryStore(job_store.configs.repo)
    if body.get("mode") == "new":
        if set(body) != {"mode", "world_id", "name", "overview"}:
            raise ConfigError("request", "新規作成には名前・概要・世界IDを指定してください", code="bad_request")
        world_id = store.create_original_world(body["world_id"], name=body["name"], overview=body["overview"])
    else:
        expected = {"world_id", "name", "from_world_id", "template_id"}
        if "mode" in body:
            expected.add("mode")
        if set(body) != expected or body.get("mode", "copy") != "copy":
            raise ConfigError("request", "作成方法と入力項目を確認してください", code="bad_request")
        world_id = store.create_world(body["world_id"], from_id=body["from_world_id"], genre_id=body["template_id"], name=body["name"])
    handler._send_json(HTTPStatus.CREATED, {"world_id": world_id})


def _edit_world(handler, world_id):
    body = _boundary_body(handler)
    jobs = _require_job_store(handler)
    from execution.world_editor import save
    result = save(LibraryStore(jobs.configs.repo), world_id, body)
    handler._send_json(HTTPStatus.OK, result)


def _save_world_basics(handler, world_id):
    jobs = _require_job_store(handler)
    body = _boundary_body(handler)
    result = LibraryStore(jobs.configs.repo).update_world_basics(world_id, body)
    handler._send_json(HTTPStatus.OK, result)


def _create_genre(handler):
    job_store = _require_job_store(handler)
    body = _boundary_body(handler)
    store = LibraryStore(job_store.configs.repo)
    if set(body) == {"template_id", "from_template_id"}:
        template_id = store.create_genre(body["template_id"], from_id=body["from_template_id"])
    else:
        from execution.genre_editor import create
        expected = {"mode", "template_id", "name", "description"}
        if body.get("mode") == "copy": expected.add("from_template_id")
        if set(body) != expected or body.get("mode") not in ("new", "copy"):
            raise ConfigError("request", "作成方法と入力項目を確認してください", code="bad_request")
        if body["mode"] == "copy" and not isinstance(body.get("from_template_id"), str):
            raise ConfigError("from_template_id", "複製元を選んでください", code="bad_request")
        template_id = create(store, body["template_id"], name=body["name"], description=body["description"], from_id=body.get("from_template_id"))
    handler._send_json(HTTPStatus.CREATED, {"template_id": template_id})


def _save_world_file(handler, world_id):
    job_store = _require_job_store(handler)
    body = _boundary_body(handler)
    path, content = _file_body(body)
    store = LibraryStore(job_store.configs.repo)
    written = store.write("world", world_id, path, content)
    handler._send_json(HTTPStatus.OK, {"path": path, "bytes": written})


def _save_genre_file(handler, genre_id):
    job_store = _require_job_store(handler)
    body = _boundary_body(handler)
    path, content = _file_body(body)
    store = LibraryStore(job_store.configs.repo)
    written = store.write("genre", genre_id, path, content)
    handler._send_json(HTTPStatus.OK, {"path": path, "bytes": written})


def _validate_world(handler, world_id):
    job_store = _require_job_store(handler)
    body = _boundary_body(handler)
    genre_id = body.get("template_id")
    if not isinstance(genre_id, str) or not genre_id:
        raise ConfigError("template_id", "ジャンルを指定してください", code="bad_request")
    store = LibraryStore(job_store.configs.repo)
    result = store.validate(job_store.configs, world_id=world_id, genre_id=genre_id)
    handler._send_json(HTTPStatus.OK, result)


def _validate_genre(handler, genre_id):
    job_store = _require_job_store(handler)
    body = _boundary_body(handler)
    world_id = body.get("project_id")
    if not isinstance(world_id, str) or not world_id:
        raise ConfigError("project_id", "世界を指定してください", code="bad_request")
    store = LibraryStore(job_store.configs.repo)
    result = store.validate(job_store.configs, world_id=world_id, genre_id=genre_id)
    handler._send_json(HTTPStatus.OK, result)


# --------------------------------------------------------------------------
# World-expansion patch approval API (WB-WORLDGROW-001 段階3b-2)
# --------------------------------------------------------------------------

_STALE_MESSAGE = "画面を開いたあとに内容が変わりました。再読み込みしてください"


def _resolve_world_patch_dirs(job_store, world_id):
    """(project_dir, template_dir) for an existing, genre-linked world --
    world_id is matched against LibraryStore.worlds()'s own listing (real
    directory names only), never joined onto a path directly, so a stray
    '..' in the URL can never leave projects/templates."""
    store = LibraryStore(job_store.configs.repo)
    world = next((w for w in store.worlds() if w["id"] == world_id), None)
    if world is None or not world.get("genre"):
        raise ConfigError("world_id", "世界がありません", code="not_found")
    return store.repo / "projects" / world["id"], store.repo / "templates" / world["genre"]


def _reject_running_job(job_store):
    # Same single-run constraint JobStore.submit() enforces -- a running/
    # queued job could be mid-simulation against exactly the patches/ this
    # request would rewrite.
    if any(job.get("state") not in TERMINAL for job in job_store.list()):
        raise ConfigError("jobs", "他の処理が実行中です", code="conflict")


def _patch_id_arg(patch_id):
    if not _PATCH_ID_RE.fullmatch(patch_id):
        raise ConfigError("patch_id", "パッチ ID の形式が不正です", code="bad_request")
    return patch_id


def _approve_body(body):
    if not isinstance(body, dict) or set(body) != {"reason", "seen"}:
        raise ConfigError("request", "reason と seen だけを指定してください", code="bad_request")
    reason = body["reason"]
    # A2 (viewer review): matches execution.world_patch_approval.approve()'s
    # own floor (len(reason.strip()) < 10 raises there) so a request that
    # passes this check never fails on reason length once it reaches approve().
    if not isinstance(reason, str) or not (10 <= len(reason.strip()) <= 500):
        raise ConfigError("reason", "承認の理由は10文字以上で書いてください", code="bad_request")
    seen = body["seen"]
    if (not isinstance(seen, dict) or set(seen) != {"patch_sha256", "gate_sha256"}
            or not isinstance(seen["patch_sha256"], str) or not isinstance(seen["gate_sha256"], str)):
        raise ConfigError("seen", "seen の形式が不正です", code="bad_request")
    return reason.strip(), seen["patch_sha256"], seen["gate_sha256"]


def _reject_body(body):
    if not isinstance(body, dict) or set(body) != {"seen"}:
        raise ConfigError("request", "seen だけを指定してください", code="bad_request")
    seen = body["seen"]
    if (not isinstance(seen, dict) or set(seen) != {"patch_sha256", "gate_sha256"}
            or not isinstance(seen["patch_sha256"], str)
            or not (seen["gate_sha256"] is None or isinstance(seen["gate_sha256"], str))):
        raise ConfigError("seen", "seen の形式が不正です", code="bad_request")
    return seen["patch_sha256"], seen["gate_sha256"]


def _reopen_body(body):
    if not isinstance(body, dict) or set(body) != {"seen"}:
        raise ConfigError("request", "seen だけを指定してください", code="bad_request")
    seen = body["seen"]
    if not isinstance(seen, dict) or set(seen) != {"head"} or not isinstance(seen["head"], str):
        raise ConfigError("seen", "seen の形式が不正です", code="bad_request")
    return seen["head"]


def _retire_body(body):
    # WB-WORLDGROW-001 段階5a: the usage table is never taken from the
    # browser (段階3bと同じ原則 -- 測定値をブラウザ送信に依存しない) -- only
    # reason/experiment/seen come from the request; retire()'s own usage
    # evidence is computed server-side just before writing.
    if not isinstance(body, dict) or set(body) != {"reason", "experiment", "seen"}:
        raise ConfigError("request", "reason・experiment・seen を指定してください", code="bad_request")
    reason = body["reason"]
    if not isinstance(reason, str) or not (10 <= len(reason.strip()) <= 500):
        raise ConfigError("reason", "淘汰の理由は10文字以上で書いてください", code="bad_request")
    experiment = body["experiment"]
    if not isinstance(experiment, str) or not experiment:
        raise ConfigError("experiment", "実験名を指定してください", code="bad_request")
    seen = body["seen"]
    if not isinstance(seen, dict) or set(seen) != {"head"} or not isinstance(seen["head"], str):
        raise ConfigError("seen", "seen の形式が不正です", code="bad_request")
    return reason.strip(), experiment, seen["head"]


def _protagonist_for(handler, experiment):
    try:
        config = data._read_json(handler.repository.safe_path(experiment, "config.json"))
    except (OSError, ValueError) as error:
        raise ConfigError("experiment", "この実験の設定を読み込めません", code="bad_request") from error
    protagonist = (config.get("preview") or {}).get("protagonist") if isinstance(config, dict) else None
    if not isinstance(protagonist, str) or not protagonist:
        raise ConfigError("experiment", "この実験の人物情報を読み込めません", code="bad_request")
    return protagonist


def _proposal_shas(project, patch_id):
    """(patch_sha256, gate_sha256) for the currently proposed patch, or
    (None, None) if either file is missing / unreadable right now."""
    folder = project / "patches" / "_proposed"
    source, gate_path = folder / f"{patch_id}.yaml", folder / f"{patch_id}.gate.json"
    try:
        patch_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        gate_sha = hashlib.sha256(gate_path.read_bytes()).hexdigest() if gate_path.is_file() else None
    except OSError:
        return None, None
    return patch_sha, gate_sha


def _approve_patch_action(handler, world_id, patch_id):
    # Body first: answering 503 with the POST body still unread makes Windows
    # reset the connection now and then, so the client never sees the 503.
    body = _boundary_body(handler)
    job_store = _require_job_store(handler)
    reason, seen_patch_sha, seen_gate_sha = _approve_body(body)
    _patch_id_arg(patch_id)
    project, template = _resolve_world_patch_dirs(job_store, world_id)
    _reject_running_job(job_store)
    # R4 (see module docstring's report notes): this freshness check runs
    # just before approve()'s own patch_lock, not inside it -- directory_lock
    # isn't reentrant (a second acquisition in this same call would itself
    # raise "conflict"). The gap it leaves is narrow and is covered by
    # approve()'s own re-validation of the sealed evidence (gate.patch_sha256
    # vs the patch bytes it reads, re-derived status, re-hashed archive, ...):
    # a rewrite in that gap either fails approve()'s own checks or is
    # functionally identical (same patch_id = same content hash).
    current_patch_sha, current_gate_sha = _proposal_shas(project, patch_id)
    if current_patch_sha != seen_patch_sha or current_gate_sha != seen_gate_sha:
        raise ConfigError("seen", _STALE_MESSAGE, code="conflict")
    try:
        # R3 (viewer review): also hand the expected hashes to approve()
        # itself, which re-checks them inside its own lock right after
        # reading both files -- closes the gap between the pre-check above
        # and approve() actually reading the (possibly since-rewritten) files.
        revision = _approve_patch(project, template, patch_id, reason, repo_root=job_store.configs.repo,
                                   expect_patch_sha256=seen_patch_sha, expect_gate_sha256=seen_gate_sha)
    except _StalePatch as error:
        raise ConfigError("seen", _STALE_MESSAGE, code="conflict") from error
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    handler._send_json(HTTPStatus.OK, {"ok": True, "patch_id": patch_id, "rev": revision["rev"]})


def _reject_patch_action(handler, world_id, patch_id):
    # Body first: answering 503 with the POST body still unread makes Windows
    # reset the connection now and then, so the client never sees the 503.
    body = _boundary_body(handler)
    job_store = _require_job_store(handler)
    seen_patch_sha, seen_gate_sha = _reject_body(body)
    _patch_id_arg(patch_id)
    project, _template = _resolve_world_patch_dirs(job_store, world_id)
    _reject_running_job(job_store)
    current_patch_sha, current_gate_sha = _proposal_shas(project, patch_id)
    if current_patch_sha != seen_patch_sha or current_gate_sha != seen_gate_sha:
        raise ConfigError("seen", _STALE_MESSAGE, code="conflict")
    try:
        _reject_patch(project, patch_id)
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    handler._send_json(HTTPStatus.OK, {"ok": True, "patch_id": patch_id})


def _reopen_patch_action(handler, world_id):
    # Body first: answering 503 with the POST body still unread makes Windows
    # reset the connection now and then, so the client never sees the 503.
    body = _boundary_body(handler)
    job_store = _require_job_store(handler)
    seen_head = _reopen_body(body)
    project, _template = _resolve_world_patch_dirs(job_store, world_id)
    _reject_running_job(job_store)
    try:
        current_head = _patch_stack_head(project)
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    if current_head != seen_head:
        raise ConfigError("seen", _STALE_MESSAGE, code="conflict")
    try:
        reopened = _reopen_patch(project)
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    if not reopened:
        raise ConfigError("patch", "戻す承認済みの拡張がありません")
    handler._send_json(HTTPStatus.OK, {"ok": True, "patch_ids": reopened})


def _retire_patch_action(handler, world_id, patch_id):
    """WB-WORLDGROW-001 段階5a: wither an already-applied patch. `experiment`
    (a run name) names which experiment's representative individuals the
    usage table is measured against -- resolved and read here, never taken
    from the browser (retire() writes that table into retire.json verbatim)."""
    # Body first: answering 503 with the POST body still unread makes Windows
    # reset the connection now and then, so the client never sees the 503.
    body = _boundary_body(handler)
    job_store = _require_job_store(handler)
    reason, experiment_name, seen_head = _retire_body(body)
    _patch_id_arg(patch_id)
    project, template = _resolve_world_patch_dirs(job_store, world_id)
    _reject_running_job(job_store)
    experiment = world_demand_view.resolve_root(handler.repository, experiment_name)
    if experiment is None:
        raise ConfigError("experiment", "実験が見つかりません", code="bad_request")
    protagonist = _protagonist_for(handler, experiment)
    try:
        active = _approved_patches(project)
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    try:
        # M1 (Opus review): a ConfigStore-prepared experiment (the only kind
        # the screen can ever expand-run) has no top-level archive.json --
        # handler.repository.archive() already resolves the catalog's
        # published/<revision>/archive.json the same verified way every
        # other candidate/archive read on this screen does.
        experiment_archive = handler.repository.archive(experiment)
    except ConfigError:
        raise
    except (OSError, ValueError, KeyError, TypeError, data.MissingResource) as error:
        raise ConfigError("experiment", "実験の記録を読み込めません", code="bad_request") from error
    try:
        usage = _patch_usage(experiment, protagonist, active, archive=experiment_archive).get(patch_id)
    except (OSError, ValueError, KeyError, TypeError, data.MissingResource) as error:
        raise ConfigError("experiment", "使用状況を計算できませんでした", code="bad_request") from error
    try:
        revision = _retire_patch(project, template, patch_id, reason, experiment=experiment,
                                  expect_head=seen_head, usage=usage)
    except _StalePatch as error:
        raise ConfigError("seen", _STALE_MESSAGE, code="conflict") from error
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    handler._send_json(HTTPStatus.OK, {"ok": True, "patch_id": patch_id, "rev": revision["rev"]})


def _export_body(body):
    if not isinstance(body, dict) or set(body) != {"experiment"}:
        raise ConfigError("request", "experiment を指定してください", code="bad_request")
    experiment = body["experiment"]
    if not isinstance(experiment, str) or not experiment:
        raise ConfigError("experiment", "実験名を指定してください", code="bad_request")
    return experiment


def _export_patch_action(handler, world_id, patch_id):
    """WB-WORLDGROW-001 段階5d: publish an applied, strongly-used patch as a
    genre asset. Same measurement steps as _retire_patch_action -- usage is
    computed here, server-side, never taken from the browser."""
    # Body first: answering 503 with the POST body still unread makes Windows
    # reset the connection now and then, so the client never sees the 503.
    body = _boundary_body(handler)
    job_store = _require_job_store(handler)
    experiment_name = _export_body(body)
    _patch_id_arg(patch_id)
    project, template = _resolve_world_patch_dirs(job_store, world_id)
    _reject_running_job(job_store)
    experiment = world_demand_view.resolve_root(handler.repository, experiment_name)
    if experiment is None:
        raise ConfigError("experiment", "実験が見つかりません", code="bad_request")
    protagonist = _protagonist_for(handler, experiment)
    try:
        active = _approved_patches(project)
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    try:
        experiment_archive = handler.repository.archive(experiment)
    except ConfigError:
        raise
    except (OSError, ValueError, KeyError, TypeError, data.MissingResource) as error:
        raise ConfigError("experiment", "実験の記録を読み込めません", code="bad_request") from error
    try:
        usage = _patch_usage(experiment, protagonist, active, archive=experiment_archive).get(patch_id)
    except (OSError, ValueError, KeyError, TypeError, data.MissingResource) as error:
        raise ConfigError("experiment", "使用状況を計算できませんでした", code="bad_request") from error
    try:
        path = _export_patch(project, template, patch_id, experiment=experiment,
                              protagonist=protagonist, usage=usage)
    except _DuplicateAssetError as error:
        raise ConfigError("patch", str(error), code="conflict") from error
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    handler._send_json(HTTPStatus.OK, {"ok": True, "patch_id": patch_id, "path": path.name})


def _import_body(body):
    if not isinstance(body, dict) or set(body) != {"entry", "seen"}:
        raise ConfigError("request", "entry と seen を指定してください", code="bad_request")
    entry = body["entry"]
    if not isinstance(entry, str) or not entry:
        raise ConfigError("entry", "資産IDを指定してください", code="bad_request")
    seen = body["seen"]
    if not isinstance(seen, dict) or set(seen) != {"head"} or not isinstance(seen["head"], str):
        raise ConfigError("seen", "seen の形式が不正です", code="bad_request")
    return entry, seen["head"]


def _import_patch_action(handler, world_id):
    """WB-WORLDGROW-001 段階5d: stage a genre asset as a new proposal in this
    world (trial_pending -- check/approve must still run here, same as any
    other proposal)."""
    # Body first: answering 503 with the POST body still unread makes Windows
    # reset the connection now and then, so the client never sees the 503.
    body = _boundary_body(handler)
    job_store = _require_job_store(handler)
    entry_id, seen_head = _import_body(body)
    _patch_id_arg(entry_id)
    project, template = _resolve_world_patch_dirs(job_store, world_id)
    _reject_running_job(job_store)
    try:
        # R4 (Opus review): the freshness check itself moved inside
        # import_patch()'s own patch_lock (expect_head) -- a pre-lock read
        # here would leave the same narrow race approve()/retire() avoid by
        # re-checking their own expect_*/expect_head inside the lock.
        rewritten = _import_patch(project, template, entry_id, repo_root=job_store.configs.repo,
                                  expect_head=seen_head)
    except _StaleImport as error:
        raise ConfigError("seen", _STALE_MESSAGE, code="conflict") from error
    except _WorldPatchError as error:
        raise ConfigError("patch", str(error)) from error
    handler._send_json(HTTPStatus.OK, {"ok": True, "patch_id": entry_id, "trigger": rewritten.get("trigger")})


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _resolve(parts, method):
    if method == "POST":
        if len(parts) == 4 and parts[:2] == ["api", "worlds"] and parts[3] == "edit":
            return _edit_world, (parts[2],)
        if len(parts) == 4 and parts[:2] == ["api", "genres"] and parts[3] in ("save", "parse", "check"):
            return _genre_editor_action, (parts[2], parts[3])
        if len(parts) == 4 and parts[:2] == ["api", "worlds"] and parts[3] == "basics":
            return _save_world_basics, (parts[2],)
        if parts == ["api", "worlds"]:
            return _create_world, ()
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "worlds" and parts[3] == "files":
            return _save_world_file, (parts[2],)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "worlds" and parts[3] == "validate":
            return _validate_world, (parts[2],)
        if parts == ["api", "genres"]:
            return _create_genre, ()
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "genres" and parts[3] == "files":
            return _save_genre_file, (parts[2],)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "genres" and parts[3] == "validate":
            return _validate_genre, (parts[2],)
        if (len(parts) == 6 and parts[:2] == ["api", "worlds"] and parts[3] == "patches"
                and parts[5] in ("approve", "reject", "retire", "export")):
            action_by_verb = {"approve": _approve_patch_action, "reject": _reject_patch_action,
                              "retire": _retire_patch_action, "export": _export_patch_action}
            return action_by_verb[parts[5]], (parts[2], parts[4])
        if (len(parts) == 5 and parts[:2] == ["api", "worlds"] and parts[3] == "patches"
                and parts[4] == "reopen"):
            return _reopen_patch_action, (parts[2],)
        if (len(parts) == 5 and parts[:2] == ["api", "worlds"] and parts[3] == "patches"
                and parts[4] == "import"):
            return _import_patch_action, (parts[2],)
        return None
    if method != "GET" or not parts:
        return None
    head = parts[0]
    if head == "worlds":
        if len(parts) == 1:
            return _worlds_list, ()
        if len(parts) == 2 and parts[1] == "new":
            return _worlds_new, ()
        if len(parts) == 2:
            return _worlds_detail, (parts[1],)
        return None
    if head == "genres":
        if len(parts) == 2 and parts[1] == "new":
            return _genres_new, ()
        if len(parts) == 2:
            return _genres_detail, (parts[1],)
        return None
    return None


def dispatch(handler, parts, method):
    route = _resolve(parts, method)
    if route is None:
        return False
    action, args = route
    try:
        action(handler, *args)
    except ConfigError as error:
        if method == "GET" and not parts[0] == "api":
            raise
        job_api.send_error(handler, error)
    except FileNotFoundError:
        if method == "GET" and parts[0] != "api":
            raise data.MissingResource("保存された記録が見つかりません")
        job_api.send_error(handler, ConfigError("resource", "対象が見つかりません", code="not_found"))
    except (data.BadRequest, data.ForbiddenPath, data.MissingResource) as error:
        if method == "GET" and parts[0] != "api":
            raise
        job_api.send_data_error(handler, error)
    except (OSError, ValueError, TypeError, KeyError):
        if method == "GET" and parts[0] != "api":
            raise OSError("保存された情報を読み取れません")
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
            "code": "storage_error", "message": "保存済み記録を処理できません",
            "field_errors": {}, "retryable": False, "current_revision": None,
        })
    return True
