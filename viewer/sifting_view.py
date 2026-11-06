"""Read-only, candidate-ID-bound data for all Sifting views."""
import math
from collections import Counter
from execution.provenance import ConfigError, contained
from execution.output_store import OutputStore, verified
from execution.output_requests import supports_story_v2
from execution.output_settings import resolve_generation
from viewer import data, pages, workbench_pages as wb, output_pages, world_usage_badge


def load(handler, run_id, *, query=None, grid=False):
    repo = handler.repository
    catalog, selections = repo.catalog, repo.selections
    snapshot = catalog.snapshot(run_id)
    selected = selections.get(run_id)
    states = {e["candidate_id"]: e for e in selected["entries"]}
    try:
        reps, rep_error = catalog.representatives(snapshot), False
    except ConfigError:
        reps, rep_error = {}, True
    root, legacy = catalog.resolve(run_id)
    store = wb._job_store(handler)
    history = catalog.history()
    record = next((r for r in history if r["run_id"] == run_id), {})
    busy = [j for j in (store.list() if store else []) if j.get("run_id") == run_id and j.get("state") in wb.RUNNING_STATES]
    running = bool(busy) or record.get("state") in wb.RUNNING_STATES
    try:
        generation = resolve_generation(getattr(handler.server, "settings_path", None))
        story_v2 = supports_story_v2(generation["backend"], generation["model"])
    except ConfigError:
        story_v2 = False
    config = None
    if record.get("config_id") and store:
        try:
            config = store.configs.get(record["config_id"])
        except (ConfigError, OSError):
            pass
    output_error = False
    try:
        recorded = store.outputs() if store else []
        output_error = any(o.get("error") for o in recorded)
        outputs = [o for o in recorded if o.get("request", {}).get("run_id") == run_id]
    except AttributeError:
        outputs = []  # Older read-only job stores have no output ledger.
    except (ConfigError, OSError, ValueError, KeyError, TypeError):
        outputs, output_error = [], True
    synopsis, statuses, output_links, synopsis_story_refs, v2_synopsis = {}, {}, {}, {}, set()
    for output in sorted(outputs, key=lambda o: o["output_id"]):
        kind = output["request"].get("kind")
        for entry in output.get("entries", []):
            cid = entry.get("candidate_id")
            statuses.setdefault(cid, {})[kind] = entry.get("status")
            output_links[cid] = f'/outputs/{pages._url_segment(output["output_id"])}#entry-{pages._url_segment(cid)}'
            if kind == "synopsize" and entry.get("status") == "ok" and entry.get("text_ref"):
                try:
                    if cid in v2_synopsis and output["request"].get("pipeline") != "story_v2":
                        continue
                    output_store = OutputStore(store.configs.control)
                    if output["request"].get("pipeline") in ("story_v1", "story_v2"):
                        from execution.story_service import read_story_output
                        result = read_story_output(output_store, output["output_id"], cid)
                        text = result["text"] if result else None
                    else:
                        text = verified(contained(output_store.folder(output["output_id"]), entry["text_ref"]), entry["text_sha256"]).decode("utf-8")
                    if not isinstance(text, str) or not text.strip():
                        raise ConfigError("story", "あらすじを読み込めません")
                    synopsis[cid] = text
                    if output["request"].get("pipeline") == "story_v2":
                        synopsis_story_refs[cid] = (output["request"].get("story_refs") or {}).get(cid)
                        v2_synopsis.add(cid)
                except (ConfigError, OSError, UnicodeDecodeError):
                    statuses[cid][kind] = "unreadable"
    synopsis_old = set()
    if synopsis_story_refs and store:
        try:
            from execution.story_store import StoryStore
            stories = StoryStore(store.configs.control)
            for cid, ref in synopsis_story_refs.items():
                current = stories.current(run_id, cid)
                if current and ref is not None:
                    now = stories.reference(current["plan"]["plan_id"], require_confirmation=False)
                    if not (isinstance(ref, dict) and all(now.get(key) == ref.get(key) for key in
                                                         ("material_id", "material_sha256", "plan_id", "plan_sha256"))):
                        synopsis_old.add(cid)
        except (ConfigError, OSError, ValueError, KeyError, TypeError):
            pass
    # Legacy summaries belong to the verified representative, never every candidate in its cell.
    if legacy:
        for cell, text in data.synopsis_texts(repo, root).items():
            if cell in reps and reps[cell] not in synopsis:
                synopsis[reps[cell]] = text
    items = []
    endings = {}
    if config:
        endings = {e["id"]: e.get("label", e["id"]) for e in config["preview"].get("world", {}).get("ending", [])}
    # 段階4b: 代表候補だけ拡張要素の使用バッジを添える（保存しない、都度導出）。
    # 実験全体で1回だけ判定・読み込むので、候補ごとの余計な解決はしない。
    # grid=False（候補一覧）ではバッジを描画しない（sifting_pages.candidate_row）
    # ので、ログを読む前にここで足切りする（Opus review 推奨1: 一覧ビューで
    # 代表候補ぶんのlayers.jsonlを読んで捨てていた）。
    usage_protagonist = (config or {}).get("preview", {}).get("protagonist") if grid else None
    usage_patches = None
    if usage_protagonist:
        usage_state = data.world_expansion_state(repo, root)
        if usage_state.get("state") == "expanded":
            usage_patches = usage_state["patches"]
    for candidate in snapshot["candidates"]["candidates"]:
        c = dict(candidate)
        cid, cell = c["candidate_id"], c.get("cell_key", "")
        state = states.get(cid, {})
        c.update(state=state.get("state", "unclassified"), note=state.get("note", ""), synopsis=synopsis.get(cid, "") or "", synopsis_old=cid in synopsis_old, synopsis_needs_review=cid in v2_synopsis)
        c["label"] = c.get("title") or wb._short_id(cid)
        q = c.get("quality")
        c["quality_text"] = f"{q:.4f}" if isinstance(q, (int, float)) and math.isfinite(q) else "—"
        c["representative"] = reps.get(cell) == cid
        c["detail_href"] = f'/exp/{pages._url_segment(snapshot["experiment_name"])}/cell/{pages._url_segment(cell)}' if c["representative"] else ""
        c["raw_href"] = f'/runs/{pages._url_segment(run_id)}/candidates/{pages._url_segment(cid)}/raw' if c.get("log", {}).get("availability") == "present" else ""
        c["availability"] = wb.AVAILABILITY_LABELS.get(c.get("log", {}).get("availability"), "原記録を確認できません")
        c["usage_html"] = ""
        if usage_patches and c["representative"] and c.get("log", {}).get("availability") == "present":
            counts = world_usage_badge.usage_counts(
                repo, root, c["log"].get("relative_path"), usage_protagonist, usage_patches)
            # sifting_pages.candidate_row が <button> の外（<article> 直下）に
            # 置くので、<details>展開版がそのまま使える（Opus review 推奨3）。
            c["usage_html"] = world_usage_badge.badge_html(counts, usage_patches)
        c["status"] = statuses.get(cid, {})
        c["output_href"] = output_links.get(cid, "")
        c["synopsis_state"] = ("旧骨格のあらすじ" if c["synopsis_old"] else "あらすじあり・要確認" if c["synopsis_needs_review"] else "あらすじあり") if c["synopsis"] else {"error":"あらすじ生成に失敗", "unknown":"あらすじ生成の結果不明", "running":"あらすじ生成中", "unreadable":"あらすじを読み込めません"}.get(c["status"].get("synopsize"), "あらすじ未生成")
        status = c["status"].get("synopsize")
        has_current_synopsis = (cid in v2_synopsis and not c["synopsis_old"]) if story_v2 else bool(c["synopsis"])
        blocked = {"unknown", "running", "pending", "unreadable"}
        if not story_v2:
            blocked.add("ok")
        c["can_synopsis"] = bool(c.get("screenable") and not running and not output_error
                                 and not has_current_synopsis and status not in blocked)
        target = c.get("ending") or c.get("target_ending")
        c["ending_text"] = endings.get(target, str(target)) if isinstance(target, str) else ("設定した結末に到達" if c.get("reached") else "設定した結末には未到達")
        items.append(c)
    counts = Counter(c["state"] for c in items)
    query = query or {}
    filter_query = {k:v for k,v in query.items() if k not in {"sort", "dir", "candidate", "view_mode", "panel", "q", "config", "run", "kind", "mode", "from_output", "ack", "cell", "publication", "arc"}}
    filters, state_filter = wb._parse_filters(wb._clean_query(filter_query))
    visible_ids = {c["candidate_id"] for c in catalog.candidates(run_id, **filters)["candidates"]}
    visible = [c for c in items if c["candidate_id"] in visible_ids and (not state_filter or c["state"] == state_filter)]
    term = (query.get("q") or [""])[0].strip().casefold()
    if term:
        visible = [c for c in visible if term in " ".join(str(c.get(k) or "") for k in ("candidate_id", "cell_key", "synopsis", "note")).casefold()]
    sort = (query.get("sort") or ["quality"])[0]
    direction = (query.get("dir") or ["desc"])[0]
    key = sort if sort in ("quality", "generation", "seed") else "quality"
    def sort_value(c):
        value = c.get(key)
        valid = isinstance(value, (int, float)) and math.isfinite(value)
        return (not valid, (-value if direction != "asc" else value) if valid else 0, c["candidate_id"])
    visible.sort(key=sort_value)
    resolved = data.resolve_genre(str((config or {}).get("preview", {}).get("world", {}).get("name", "")))
    categories, bins, declared_arc_bins = data.qd_axes(resolved[2] if resolved else None)
    qd = (config or {}).get("preview", {}).get("qd", {})
    categories = list(qd.get("categories") or categories)
    bins = list(qd.get("volatility_bins") or bins)
    arc_bins = list(qd.get("arc_bins") or declared_arc_bins)
    for cell in reps:
        parts = cell.split("|")
        category, bin_name = parts[0], (parts[1] if len(parts) > 1 else "")
        if category not in categories: categories.append(category)
        if bin_name not in bins: bins.append(bin_name)
        if len(parts) > 2 and parts[2] not in arc_bins:
            arc_bins.append(parts[2])

    # WB-GROWTH-001 S3: reps is keyed by the archive's raw cell (2 or 3
    # parts); grid_reps collapses it to one representative per
    # (category, volatility_bin) pair for the grid table -- "all" (the
    # default) picks the best-quality one across arc bins, a specific
    # arc value narrows to just that bin. reps itself (exact cell keys)
    # still decides c["representative"]/detail hrefs, unaffected by grid.
    arc_enabled = any(isinstance(key, str) and key.count("|") == 2 for key in reps)
    arc_filter = (query.get("arc") or ["all"])[0] if arc_enabled else "all"
    if arc_filter not in ("all", "none", "small", "large"):
        arc_filter = "all"
    if not arc_enabled:
        grid_reps = reps
    elif arc_filter == "all":
        items_by_id = {c["candidate_id"]: c for c in items}
        best: dict[str, tuple[str, float]] = {}
        # Deterministic tie-break: iterate cell_key ascending (never dict/
        # insertion order) and only replace on a strictly higher quality, so
        # equal-quality candidates keep whichever sorts first. A candidate
        # with no numeric quality is excluded from the comparison entirely
        # (never wins a pair merely for being visited first).
        for cell_key in sorted(reps):
            cid = reps[cell_key]
            parts = cell_key.split("|")
            if len(parts) != 3:
                continue
            pair = f"{parts[0]}|{parts[1]}"
            candidate = items_by_id.get(cid)
            quality = candidate.get("quality") if candidate else None
            if not isinstance(quality, (int, float)):
                continue
            current = best.get(pair)
            if current is None or quality > current[1]:
                best[pair] = (cid, quality)
        grid_reps = {pair: cid for pair, (cid, _) in best.items()}
    else:
        grid_reps = {
            f"{parts[0]}|{parts[1]}": cid
            for cell_key, cid in reps.items()
            for parts in [cell_key.split("|")]
            if len(parts) == 3 and parts[2] == arc_filter
        }
    return dict(run_id=run_id, name=snapshot["experiment_name"], label=(config or {}).get("label") or snapshot["experiment_name"],
                world=wb._config_world(config) if config else None, config=config, legacy=legacy,
                revision=selected["revision"], publication=snapshot["revision"], items=items, visible=visible,
                counts=dict(counts), reps=reps, rep_error=rep_error, categories=categories, bins=bins,
                arc_bins=arc_bins, arc_enabled=arc_enabled, arc_filter=arc_filter, grid_reps=grid_reps,
                output_error=output_error, running=running, busy=busy, history=history, query=query)
