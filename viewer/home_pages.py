"""Read-only home library. Summaries come only from saved world settings."""
from __future__ import annotations

from datetime import datetime, timezone
import html
import math

import yaml

from execution.library import LibraryStore
from execution.provenance import contained, identifier, read_json
from viewer import data, pages

def E(value):
    return html.escape(str(value if value is not None else ""), quote=True)

U = pages._url_segment


def world_details(store, world):
    """Do not infer stories or overwrite a deliberately blank overview."""
    try:
        value = yaml.safe_load(store.read("world", world["id"], "world.yaml"))
        value = value if isinstance(value, dict) else {}
    except (ValueError, OSError, yaml.YAMLError):
        value = {}
    zones = value.get("zones")
    zones = zones if isinstance(zones, list) else []
    names = [str(z["name"]) for z in zones if isinstance(z, dict) and z.get("name")]
    overview = value.get("overview")
    if isinstance(overview, str) and overview.strip():
        description = overview.strip()
    elif "overview" in value:
        description = "世界の概要はまだ設定されていません。"
    elif names:
        description = "舞台：" + "、".join(names[:5]) + (" ほか" if len(names) > 5 else "") + "。"
    else:
        description = "人物や場所、物語の始まりを設定して、この世界を育てましょう。"
    period = value.get("time")
    days = period.get("days") if isinstance(period, dict) else None
    day_text = f"{days}日間" if type(days) is int and days > 0 else "期間 未設定"
    return description, len(zones), day_text


def recent_execution(jobs):
    """Read saved receipts without JobStore.list/get reconciliation or writes."""
    if jobs is None or not getattr(jobs, "root", None) or not jobs.root.is_dir():
        return ""
    entries = []
    for folder in jobs.root.iterdir():
        if not folder.is_dir() or not folder.name.startswith("job-"):
            continue
        try:
            identifier(folder.name, "job_id")
            row = read_json(contained(jobs.root, folder.name + "/job.json"))
            if row.get("job_id") != folder.name or row.get("kind", "evolve") != "evolve":
                continue
            stamp = row.get("created_at")
            if isinstance(stamp, bool):
                continue
            stamp = float(stamp) if isinstance(stamp, (int, float)) else datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            if not math.isfinite(stamp) or stamp <= 0:
                continue
            date = datetime.fromtimestamp(stamp, timezone.utc).astimezone().strftime("%Y/%m/%d %H:%M")
            entries.append((stamp, folder.name, date, row))
        except (ValueError, OSError, AttributeError, TypeError, OverflowError):
            continue
    if not entries:
        return ""
    _, jid, date, row = max(entries, key=lambda v: (v[0], v[1]))
    try:
        config = jobs.configs.get(row.get("config_id"))
        name = config.get("preview", {}).get("world_name") or config.get("label") or "世界名の記録なし"
    except (ValueError, OSError, AttributeError, TypeError):
        name = "世界名の記録なし"
    return ('<aside class="hl-recent" aria-label="最近の実行"><span>最近の実行</span>'
            f'<strong>{E(name)}</strong><time>{E(date)} 作成</time>'
            f'<a href="/jobs/{U(jid)}">実行を開く →</a></aside>')



def world_people(store, world_id):
    names = []
    for rel in store.world_files(world_id):
        if not rel.startswith("subjects/"):
            continue
        try:
            subject = yaml.safe_load(store.read("world", world_id, rel))
            if isinstance(subject, dict) and subject.get("id"):
                names.append(str(subject["id"]))
        except (ValueError, OSError, yaml.YAMLError):
            continue
    return " ".join(names)


def world_card(store, world, genres):
    wid = world["id"]
    genre = world.get("genre") or ""
    genre_name = genres.get(genre, {}).get("name") or genre or "ジャンル未設定"
    description, places, period = world_details(store, world)
    protagonist = world.get("protagonist") or "未設定"
    antagonist = world.get("antagonist") or "未設定"
    name = world.get("name") or wid
    search = " ".join(str(v) for v in (name, wid, genre_name, protagonist, antagonist, description, world_people(store, wid)))
    return (f'<article class="world-card" data-home-card data-search="{E(search)}" data-genre="{E(genre)}">'
            f'<span class="hl-tag">{E(genre_name)}</span><h2>{E(name)}</h2>'
            f'<p class="hl-description">{E(description)}</p>'
            f'<p class="hl-facts">人物 {E(world["subjects"])}人 <span>・</span> 場所 {places}か所 <span>・</span> {E(period)}</p>'
            f'<p class="hl-roles">主人公 <strong>{E(protagonist)}</strong><span>／</span>敵役 <strong>{E(antagonist)}</strong></p>'
            f'<a class="hl-open" href="/worlds/{U(wid)}" aria-label="{E(name)}の世界を開く">世界を開く →</a></article>')


def genre_card(genre, worlds, editable):
    name = genre.get("name") or genre["id"]
    description = genre.get("description") or "人物が選べる行動と、その結果を決める共通のルールです。"
    used = [worlds.get(w, w) for w in genre.get("used_by", [])]
    search = " ".join([name, genre["id"], description, *used])
    action = f'<a class="hl-open" href="/genres/{U(genre["id"])}">ジャンルを開く →</a>' if editable else '<p class="hl-readonly">編集はWorldBloom Studioで利用できます。</p>'
    expansions = genre.get("expansions") or 0
    return (f'<article class="hl-genre-card" data-home-card data-search="{E(search)}">'
            f'<span class="hl-tag">共通のルール</span><h2>{E(name)}</h2>'
            f'<p class="hl-description">{E(description)}</p>'
            f'<p class="hl-facts">使っている世界 {len(used)}件 <span>・</span> 資産 {E(expansions)}件</p>'
            f'<p class="hl-roles">{E("、".join(used) if used else "まだ使われていません")}</p>{action}</article>')


def render(repository, *, job_store=None):
    store = LibraryStore(job_store.configs.repo if job_store else data.ROOT)
    notice = ""
    try:
        worlds, genres = store.worlds(), store.genres()
    except (ValueError, OSError, KeyError, TypeError):
        worlds, genres = [], []
        notice = '<p class="hl-notice" role="status">世界の一覧を読み込めませんでした。保存場所を確認してから、ページを開き直してください。</p>'
    genre_map = {g["id"]: g for g in genres}
    world_map = {w["id"]: w.get("name") or w["id"] for w in worlds}
    options = {w.get("genre") or "": genre_map.get(w.get("genre"), {}).get("name") or w.get("genre") or "ジャンル未設定" for w in worlds}
    select = "".join(f'<option value="{E(key or "__unset__")}">{E(value)}</option>' for key, value in sorted(options.items()))
    create = '<a class="hl-primary" href="/worlds/new">＋ 新しい世界を作る</a>' if job_store else '<span class="hl-readonly">閲覧専用</span>'
    new_genre = '<a class="hl-open hl-new-genre" href="/genres/new">＋ 新しいジャンルを作る</a>' if job_store else ""
    cards = "".join(world_card(store, w, genre_map) for w in worlds)
    genre_cards = "".join(genre_card(g, world_map, job_store is not None) for g in genres)
    body = ('<div class="hl-shell" data-home-library><header class="hl-heading"><div><h1>世界を選ぶ</h1>'
            '<p>世界を開いて、人物や場所、物語の始まりを確かめましょう。</p></div>' + create + '</header>'
            + notice + recent_execution(job_store)
            + '<div class="hl-toolbar"><div class="hl-tabs" role="tablist" aria-label="ライブラリー">'
            f'<button id="hl-world-tab" role="tab" aria-selected="true" aria-controls="worlds" data-home-tab="worlds">世界 <span>{len(worlds)}</span></button>'
            f'<button id="hl-genre-tab" role="tab" aria-selected="false" aria-controls="genres" tabindex="-1" data-home-tab="genres">ジャンル <span>{len(genres)}</span></button></div>'
            '<div class="hl-filters"><label><span class="hl-sr">世界名・人物で検索</span><input data-home-search type="search" placeholder="世界名・人物で検索"></label>'
            '<label data-home-genre-label><span class="hl-sr">ジャンルで絞り込み</span><select data-home-genre><option value="">すべてのジャンル</option>'
            + select + '</select></label><span class="hl-count" data-home-count role="status" aria-live="polite">'
            f'{len(worlds)}件</span></div></div><div class="hl-scroll">'
            '<section id="worlds" role="tabpanel" aria-labelledby="hl-world-tab"><div class="hl-grid">' + cards + '</div>'
            + ('' if worlds else '<div class="hl-empty"><h2>まだ世界がありません</h2><p>新しい世界を作るところから始めましょう。</p></div>')
            + '</section><section id="genres" role="tabpanel" aria-labelledby="hl-genre-tab" hidden>'
            '<div class="hl-genre-intro"><p>ジャンルは、行動とその結果を決める共通のルールです。</p>' + new_genre + '</div>'
            '<div class="hl-grid">' + genre_cards + '</div>'
            + ('' if genres else '<div class="hl-empty"><h2>ジャンルがありません</h2></div>')
            + '</section><div class="hl-empty" data-home-empty hidden><h2>条件に合うものがありません</h2>'
            '<p>検索する言葉やジャンルを変えてみてください。</p><button type="button" data-home-reset>絞り込みを解除</button></div></div>'
            '<footer class="hl-footer">世界の設定を編集しても、保存済みの実行結果は変わりません。</footer></div>')
    doc = pages.document("世界を選ぶ", body, phase="world", job_store=job_store,
                         show_phase_band=False, is_home=True, page_class="home-library")
    return doc.replace("</head>", '<link rel="stylesheet" href="/static/home-workspace.css"><script src="/static/home-workspace.js" defer></script></head>')
