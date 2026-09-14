"""World and genre library: HTML pages and JSON APIs (WB-UI-010 Stage 2).

Every persistent change goes through execution.library.LibraryStore; this
module only renders HTML, parses requests and enforces the POST boundary
(job_api.boundary). Screen rendering never calls an LLM. Semantic validation
of a world/genre pair is never reimplemented here -- /api/*/validate calls
straight through to execution.configs.ConfigStore.preview via LibraryStore.
"""
from __future__ import annotations

from http import HTTPStatus

import yaml

from execution.library import LibraryStore
from execution.provenance import ConfigError
from viewer import data, pages, job_api, world_graph
from viewer.workbench_pages import _guidance_page, _query

_escape = pages._escape
_url = pages._url_segment

GENRE_FILE_LABELS = {
    "action_graph.yaml": "行動グラフ", "canon.yaml": "正典プライア", "effects.yaml": "効果表",
    "qd.yaml": "QD軸", "rules.yaml": "ルール",
    "action_graph.antagonist.yaml": "行動グラフ（敵役）", "canon.antagonist.yaml": "正典プライア（敵役）",
}

NOTE = ('<p class="library-note">保存しても過去の実行設定と実験結果は変わりません'
        '（実行時に写しを取ります）。</p>')


def _job_store(handler):
    return getattr(handler.server, "job_store", None)


# --------------------------------------------------------------------------
# Small render helpers
# --------------------------------------------------------------------------

def _editor_block(rel, content, *, label=None):
    heading = f"{label}（{rel}）" if label else rel
    return (
        f'<details open class="editor"><summary>{_escape(heading)}</summary>'
        f'<textarea data-file="{_escape(rel)}" rows="16" spellcheck="false">{_escape(content)}</textarea>'
        f'<button type="button" data-action="save-file" data-path="{_escape(rel)}">保存</button>'
        f'<span data-save-status data-for="{_escape(rel)}"></span>'
        f'<span class="field-error" data-error-for="{_escape(rel)}" role="alert"></span>'
        "</details>"
    )


def _world_row(world, run_count):
    genre = world["genre"]
    edit_href = f"/worlds/{_url(world['id'])}"
    run_href = (
        f"/configs/new?project={_url(world['id'])}&template={_url(genre)}"
        if genre else f"/configs/new?project={_url(world['id'])}"
    )
    runs_cell = (
        f'<a href="{edit_href}#experiments">{run_count} 件</a>' if run_count else "—"
    )
    return (
        "<tr>"
        f'<td><a href="{edit_href}">{_escape(world["name"] or world["id"])}</a></td>'
        f'<td>{_escape(world["id"])}</td>'
        f'<td>{_escape(genre) if genre else "—"}</td>'
        f'<td>{_escape(world["subjects"])}</td>'
        f'<td>{_escape(world["protagonist"])} / {_escape(world["antagonist"])}</td>'
        f'<td>{runs_cell}</td>'
        '<td class="wb-actions">'
        f'<a href="{edit_href}">開く</a>'
        f'<a href="{run_href}">この世界で新しい実験を回す</a>'
        "</td></tr>"
    )


def _genre_row(genre):
    used = ", ".join(genre["used_by"]) if genre["used_by"] else "—"
    return (
        "<tr>"
        f'<td><a href="/genres/{_url(genre["id"])}">{_escape(genre["id"])}</a></td>'
        f'<td>{_escape(", ".join(genre["files"]))}</td>'
        f'<td>{_escape(used)}</td>'
        f'<td class="wb-actions"><a href="/genres/{_url(genre["id"])}">編集</a></td>'
        "</tr>"
    )


def render_worlds_hub(worlds, genres, *, run_counts, can_create):
    world_table = (
        '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
        f'<th>名前</th><th>ID</th><th title="{_escape(pages.TERM_HELP["genre"])}">ジャンル</th>'
        "<th>人物数</th><th>主人公/敵役</th><th>実験</th><th>操作</th>"
        f'</tr></thead><tbody>{"".join(_world_row(w, run_counts.get(w["name"], 0)) for w in worlds)}</tbody></table></div>'
    ) if worlds else "<p>世界がありません。</p>"
    genre_table = (
        '<div class="grid-wrap"><table class="wb-table"><thead><tr>'
        "<th>ID</th><th>ファイル</th><th>使っている世界</th><th>操作</th>"
        f'</tr></thead><tbody>{"".join(_genre_row(g) for g in genres)}</tbody></table></div>'
    ) if genres else "<p>ジャンルがありません。</p>"
    create_genre = '<p><a href="/genres/new">新しいジャンルを作る</a></p>' if can_create else ""
    return (
        '<section class="card"><h2>世界</h2>' + world_table + "</section>"
        + '<section class="card" id="genres"><h2>ジャンル</h2>' + genre_table + create_genre + "</section>"
    )


def render_world_new_form(worlds, genres, *, from_id, genre_id):
    world_options = "".join(
        f'<option value="{_escape(w["id"])}"{" selected" if w["id"] == from_id else ""}>'
        f'{_escape(w["name"] or w["id"])}</option>'
        for w in worlds
    )
    genre_options = "".join(
        f'<option value="{_escape(g["id"])}"{" selected" if g["id"] == genre_id else ""}>{_escape(g["id"])}</option>'
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
        f'<option value="{_escape(g["id"])}"{" selected" if g["id"] == from_id else ""}>{_escape(g["id"])}</option>'
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


def _overview_panel(world, subjects):
    goals = world_graph.world_summary(
        subjects, protagonist=world["protagonist"], antagonist=world["antagonist"],
    )
    return (
        f'<p>名前: {_escape(world["name"])} · ジャンル: {_escape(world["genre"]) if world["genre"] else "—"}'
        f' · 人物数: {_escape(world["subjects"])}'
        f' · 主人公/敵役: {_escape(world["protagonist"])} / {_escape(world["antagonist"])}</p>'
        + goals
    )


def _characters_panel(world, subjects):
    svg = world_graph.relation_svg(subjects, protagonist=world["protagonist"], antagonist=world["antagonist"])
    table = world_graph.character_table(subjects, protagonist=world["protagonist"], antagonist=world["antagonist"])
    return (
        f'<p class="muted">人物数: {_escape(world["subjects"])}</p>'
        + svg + table
        + '<p class="muted">線の色: 緑=好意 / 赤=敵意 / 灰=中立。'
        "太さ=好感度の強さ、濃さ=認知度。"
        "線にカーソルを合わせると双方向の値が出ます。</p>"
    )


def _places_panel(world_yaml):
    zones = world_yaml.get("zones") or []
    routes = world_yaml.get("routes") or {}
    return world_graph.zone_list_html(zones) + world_graph.zone_svg(zones, routes)


def _period_panel(world_yaml):
    time_info = world_yaml.get("time") or {}
    days = time_info.get("days")
    slots = time_info.get("slots") or []
    day_count = _escape(days) if days else "—"
    return (
        "<h3>1日の時間帯</h3>" + world_graph.day_cycle_svg(slots)
        + f"<h3>日程（{day_count}日間）</h3>" + world_graph.calendar_grid_html(days)
    )


def _experiments_card(repository, world_name, job_store):
    return (
        '<section class="card" id="experiments"><h2>この世界の実験</h2>'
        + pages.world_runs_block(repository, world_name, job_store)
        + "</section>"
    )


def _editor_group(store, world, job_store):
    if job_store is None:
        return '<p class="library-note">編集・検証には <code>--control</code> 付きで起動してください。</p>'
    world_id = world["id"]
    files = store.world_files(world_id)
    contents = {rel: store.read("world", world_id, rel) for rel in files}
    subject_files = [rel for rel in files if rel.startswith("subjects/")]
    template_content = contents[subject_files[0]] if subject_files else ""
    genre_options = "".join(
        f'<option value="{_escape(g["id"])}"{" selected" if g["id"] == world["genre"] else ""}>{_escape(g["id"])}</option>'
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
    editors = "".join(_editor_block(rel, contents[rel]) for rel in files)
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
        f'<div data-wb="library" data-kind="world" data-owner="{_escape(world_id)}">'
        '<details class="editor-group">'
        "<summary>ファイルを編集（world.yaml と subjects/…）</summary>"
        + validate_form
        + f'<section class="card"><h2>ファイル</h2>{editors}</section>'
        + add_subject
        + "</details></div>"
    )


def render_world_detail(repository, world, store, job_store):
    run_href = (
        f"/configs/new?project={_url(world['id'])}&template={_url(world['genre'])}"
        if world["genre"] else f"/configs/new?project={_url(world['id'])}"
    )
    world_yaml = _world_yaml_mapping(store.repo, world["id"])
    subjects = world_graph.load_subjects(store.repo / "projects" / world["id"])
    world_name = str(world["name"] or world["id"])
    panels = [
        ("概要", _overview_panel(world, subjects)),
        ("登場人物", _characters_panel(world, subjects)),
        ("場所", _places_panel(world_yaml)),
        ("期間", _period_panel(world_yaml)),
    ]
    overview_card = (
        '<section class="card">'
        f'<p class="actions"><a class="button primary" href="{_escape(run_href)}">この世界で新しい実験を回す</a></p>'
        + pages.tabs("world", panels)
        + "</section>"
    )
    return (
        overview_card
        + _experiments_card(repository, world_name, job_store)
        + _editor_group(store, world, job_store)
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
    body = render_world_new_form(worlds, genres, from_id=from_id, genre_id=genre_id)
    handler._send_html(pages.document(
        "新しい世界を作る", body, crumbs=[("新しい世界", "/worlds/new")], phase="world",
    ))


def _worlds_detail(handler, world_id):
    job_store = _job_store(handler)
    repo = job_store.configs.repo if job_store is not None else data.ROOT
    store = LibraryStore(repo)
    world = next((w for w in store.worlds() if w["id"] == world_id), None)
    if world is None:
        raise ConfigError("world_id", "世界がありません", code="not_found")
    label = world["name"] or world_id
    genre = world.get("genre")
    run_href = f"/configs/new?project={_url(world_id)}"
    if genre:
        run_href += f"&template={_url(genre)}"
    body = render_world_detail(handler.repository, world, store, job_store)
    handler._send_html(pages.document(
        f"世界: {label}", body,
        crumbs=[(label, f"/worlds/{_url(world_id)}")],
        phase="world", world={"id": world_id, "name": label},
        lead="GA 実行前のベース設定です。人物と関係を確かめ、必要なら編集してから実験に使います。",
        next_action=("この世界で実験を回す →", run_href),
    ))


def _genres_new(handler):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    store = LibraryStore(job_store.configs.repo)
    genres = store.genres()
    genre_ids = {g["id"] for g in genres}
    query = _query(handler)
    from_id = query.get("from", [None])[0]
    if from_id not in genre_ids:
        from_id = genres[0]["id"] if genres else None
    body = render_genre_new_form(genres, from_id=from_id)
    handler._send_html(pages.document(
        "新しいジャンルを作る", body, crumbs=[("新しいジャンル", "/genres/new")], phase="world",
    ))


def _genres_detail(handler, genre_id):
    job_store = _job_store(handler)
    if job_store is None:
        handler._send_html(_guidance_page(phase="world"))
        return
    store = LibraryStore(job_store.configs.repo)
    genre = next((g for g in store.genres() if g["id"] == genre_id), None)
    if genre is None:
        raise ConfigError("template_id", "ジャンルがありません", code="not_found")
    contents = {rel: store.read("genre", genre_id, rel) for rel in genre["files"]}
    body = render_genre_detail(genre, contents, store.worlds())
    handler._send_html(pages.document(
        f"ジャンル: {genre_id}", body,
        crumbs=[(genre_id, f"/genres/{_url(genre_id)}")],
        phase="world",
        lead="このジャンルの文法を編集し、世界を指定して検証します。",
        next_action=("世界一覧へ →", "/worlds"),
    ))


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
    if set(body) != {"world_id", "name", "from_world_id", "template_id"}:
        raise ConfigError("request", "world_id, name, from_world_id, template_idを指定してください", code="bad_request")
    store = LibraryStore(job_store.configs.repo)
    world_id = store.create_world(
        body["world_id"], from_id=body["from_world_id"], genre_id=body["template_id"], name=body["name"])
    handler._send_json(HTTPStatus.CREATED, {"world_id": world_id})


def _create_genre(handler):
    job_store = _require_job_store(handler)
    body = _boundary_body(handler)
    if set(body) != {"template_id", "from_template_id"}:
        raise ConfigError("request", "template_id, from_template_idを指定してください", code="bad_request")
    store = LibraryStore(job_store.configs.repo)
    template_id = store.create_genre(body["template_id"], from_id=body["from_template_id"])
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
# Dispatch
# --------------------------------------------------------------------------

def _resolve(parts, method):
    if method == "POST":
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
        job_api.send_error(handler, error)
    except FileNotFoundError:
        job_api.send_error(handler, ConfigError("resource", "対象が見つかりません", code="not_found"))
    except (OSError, ValueError, TypeError, KeyError):
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
            "code": "storage_error", "message": "保存済み記録を処理できません",
            "field_errors": {}, "retryable": False, "current_revision": None,
        })
    return True
