"""Execution-settings editor; saving still uses the existing config API."""
import json
from viewer import pages, run_summary

E, U = pages._escape, pages._url_segment


def render(handler, values, *, projects, templates, worlds, parent=None, rationality=None):
    from viewer import workbench_pages as wb, library_pages, run_browse
    store = wb._job_store(handler)
    from execution.library import LibraryStore
    genre_names = {g["id"]: g.get("name") or g["id"] for g in LibraryStore(store.configs.repo).genres()}
    selected = values["project_id"]
    world = next((w for w in worlds if w["id"] == selected), None)
    names = {w["id"]: w.get("name") or w["id"] for w in worlds}
    metadata = {}
    for w in worlds:
        definition = library_pages._world_yaml_mapping(store.configs.repo, w["id"])
        endings = definition.get("ending") or []
        labels = {e["id"]: e.get("label") or e["id"] for e in endings if isinstance(e, dict) and "id" in e}
        target = definition.get("target_ending") or []
        if isinstance(target, str):
            target = [target]
        metadata[w["id"]] = {"name": names[w["id"]], "genre": w.get("genre"),
            "ending": "、".join(labels.get(k, k) for k in target) or "世界の既定の結末を使います。",
            "labels": labels, "protagonist": w.get("protagonist") or "未設定", "antagonist": w.get("antagonist") or "未設定"}
    parent_id = parent["config_id"] if parent else None
    parent_attr = f' data-parent="{E(parent_id)}"' if parent else ""
    def text(label, field, **kwargs):
        return wb._text_field(label, field, values[field], **kwargs)
    def number(label, field, **kwargs):
        return wb._number_field(label, field, values[field], **kwargs)
    if parent:
        world_field = (f'<input type="hidden" name="project_id" value="{E(selected)}">'
            f'<div class="rs-fixed"><span>世界</span><strong>{E(names.get(selected, selected))}</strong></div>')
        genre_field = (f'<input type="hidden" name="template_id" value="{E(values["template_id"])}">'
            f'<p>ジャンル：{E(values["template_id"])}（複製元と同じ）</p>')
    else:
        options = '<option value="">世界を選ぶ…</option>' + ''.join(
            f'<option value="{E(p)}" data-genre="{E(metadata.get(p, {}).get("genre") or "")}"'
            + (' selected' if p == selected else '') + f'>{E(names.get(p, p))}</option>' for p in projects)
        world_field = ('<div class="field"><label for="f-project_id">世界</label>'
            f'<select id="f-project_id" name="project_id" data-field="project_id" required>{options}</select>'
            '<span class="field-error" data-error-for="project_id" role="alert"></span></div>')
        genre_field = wb._select_field("ジャンル", "template_id", ["", *templates], values["template_id"])
        for ident, label in genre_names.items():
            genre_field = genre_field.replace(">"+E(ident)+"</option>", ">"+E(label)+"</option>")
    custom = bool(values["evolution.target_ending"])
    ending = ('<div class="rs-ending field"><label for="rs-ending-mode">目指す結末</label>'
        '<div class="wb-ending-mode rs-ending-mode"><select id="rs-ending-mode" data-ending-mode>'
        '<option value="default"' + ('' if custom else ' selected') + '>世界の既定を使う</option>'
        '<option value="custom"' + (' selected' if custom else '') + '>別の結末を指定</option></select></div>'
        + text("結末ID", "evolution.target_ending", placeholder="複数ある場合はカンマ区切り")
        + '<p data-ending-description class="rs-muted"></p></div>')
    picker_options = '<option value="">保存済みの条件から選ぶ…</option>' + ''.join(
        f'<option value="{E(c["config_id"])}"' + (' selected' if c["config_id"] == parent_id else '')
        + f'>{E(c["label"])}</option>' for c in store.configs.list() if not selected or c["project_id"] == selected)
    name = text("設定名", "label", required=True, placeholder="例：放課後の約束・探索テスト")
    scale = ''.join(number(label, f"evolution.{key}", unit=unit, min_value=1) for label, key, unit in
        (("世代数", "generations", "世代"), ("1世代の個体数", "population", "個体"), ("個体ごとの評価回数", "seeds", "回")))
    keeps = (("all", "すべての結果"), ("reached", "結末に到達した結果"), ("exemplar", "個体ごとに代表1件"))
    keep = ('<div class="field rs-keep"><label for="f-evolution.keep">結果の保存</label>'
        '<select id="f-evolution.keep" name="evolution.keep" data-field="evolution.keep">'
        + ''.join(f'<option value="{k}"' + (' selected' if values["evolution.keep"] == k else '') + f'>{label}</option>' for k, label in keeps)
        + '</select><span class="field-error" data-error-for="evolution.keep" role="alert"></span></div>')
    toggles = ''.join(wb._checkbox_field(label, f"evolution.{key}", values[f"evolution.{key}"])
        for label, key in (("説明記録", "record_explanations"), ("共進化", "coevolve"), ("メタ進化", "meta_evolution")))
    if rationality is not None:
        total = wb._as_int(values["evolution.generations"]) * wb._as_int(values["evolution.population"]) * wb._as_int(values["evolution.seeds"])
        # S2 (Opus review, WB-JEV-002 merge follow-up): _rationality_section
        # already renders its own <section class="cfg-sec">...<h2>, so it
        # must not be wrapped in another <section><h2> -- that produced a
        # nested <section> with two consecutive <h2>s. heading_prefix folds
        # this form's "04. " numbering into that single h2 instead.
        rationality_section = wb._rationality_section(
            values, rationality,
            # Opus review (render_config_form): match execution/configs.py's
            # _describe() planned_seed_evaluations -- a coevolve run judges a
            # protagonist pass and a separate antagonist pass.
            total_runs=total * (2 if values["evolution.coevolve"] else 1),
            wall_seconds=wb._as_int(values["execution_limits.wall_seconds"]),
            heading_prefix="04. ",
        )
    else:
        rationality_section = ""
    advanced = ('<details class="cfg-adv"><summary>詳細設定 <span data-limit-summary></span></summary>'
        + genre_field + '<div class="rs-scale">'
        + number("乱数の開始値", "evolution.seed_base") + number("進化の乱数", "evolution.ga_seed")
        + number("並列数", "evolution.processes", min_value=1) + '</div>'
        + number("実行時間の上限", "execution_limits.wall_seconds", min_value=1, unit="秒")
        + '<p>共進化：敵役も並行して進化させます。メタ進化：ルールの有効・無効も探索します。</p>'
        '<p>説明記録を残すと、候補の選択・根拠・代償・転機を確認できます。</p>'
        '<p>結末に到達した結果がない場合は、最良個体の結果を保存します。</p></details>')
    back = f'/worlds/{U(selected)}' if selected else '/'
    body = ('<div class="rw-shell rs-shell">' + run_browse.navigation("settings", world=world, config=parent, store=store)
        + f'<form class="rs-form" data-run-settings data-wb="config-form"{parent_attr} data-worlds="{E(json.dumps(metadata, ensure_ascii=False))}">'
        '<div class="rs-editor"><header class="rs-heading"><h1>実行設定</h1><p>探索する条件を決める</p></header>'
        '<div class="rs-name">' + name + f'<label class="rs-picker">保存済みの条件<select data-saved-config>{picker_options}</select></label></div>'
        '<p class="form-error" data-form-error role="alert" tabindex="-1"></p>'
        '<section><h2>01. 世界と結末</h2><div class="rs-world">' + world_field
        + f'<a data-world-link href="{E(back)}">世界設定を見る ↗</a></div>' + ending + '<p data-world-facts class="rs-muted"></p></section>'
        '<section><h2>02. 探索の規模</h2><div class="rs-scale">' + scale
        + '</div><p class="rs-muted">同じ個体を、異なる初期条件で評価します。1世代あたり <span data-per-gen></span> 回。</p></section>'
        '<section><h2>03. 保存と進化</h2>' + keep + '<div class="rs-toggles">' + toggles + '</div></section>'
        + rationality_section + advanced + '</div>'
        + run_summary.render({k.removeprefix("evolution."): v for k, v in values.items() if k.startswith("evolution.")}, editable=True)
        +
        '<footer class="rw-footer rs-footer">'
        f'<a data-world-back href="{E(back)}">← 世界設定へ戻る</a>'
        '<span>新しい条件として保存します。実行は次の画面で開始します。</span>'
        '<button type="submit">保存して実行条件へ →</button></footer></form></div>')
    doc = pages.document("実行設定", body, phase="run", world=world, job_store=store, page_class="run-observer")
    handler._send_html(doc.replace('</head>', '<link rel="stylesheet" href="/static/run-workspace.css">'
        '<link rel="stylesheet" href="/static/run-settings.css"><script src="/static/run-settings.js" defer></script></head>'))
