"""Read-only, candidate-ID-bound data for all Sifting views."""
import math
from collections import Counter
from execution.provenance import ConfigError, contained
from execution.output_store import OutputStore, verified
from viewer import data, pages, workbench_pages as wb, output_pages


def load(handler, run_id, *, query=None):
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
    synopsis, statuses, output_links = {}, {}, {}
    for output in sorted(outputs, key=lambda o: o["output_id"]):
        kind = output["request"].get("kind")
        for entry in output.get("entries", []):
            cid = entry.get("candidate_id")
            statuses.setdefault(cid, {})[kind] = entry.get("status")
            output_links[cid] = f'/outputs/{pages._url_segment(output["output_id"])}#entry-{pages._url_segment(cid)}'
            if kind == "synopsize" and entry.get("status") == "ok" and entry.get("text_ref"):
                try:
                    output_store = OutputStore(store.configs.control)
                    synopsis[cid] = verified(contained(output_store.folder(output["output_id"]), entry["text_ref"]), entry["text_sha256"]).decode("utf-8")
                except (ConfigError, OSError, UnicodeDecodeError):
                    statuses[cid][kind] = "unreadable"
    # Legacy summaries belong to the verified representative, never every candidate in its cell.
    if legacy:
        for cell, text in data.synopsis_texts(repo, root).items():
            if cell in reps and reps[cell] not in synopsis:
                synopsis[reps[cell]] = text
    items = []
    endings = {}
    if config:
        endings = {e["id"]: e.get("label", e["id"]) for e in config["preview"].get("world", {}).get("ending", [])}
    for candidate in snapshot["candidates"]["candidates"]:
        c = dict(candidate)
        cid, cell = c["candidate_id"], c.get("cell_key", "")
        state = states.get(cid, {})
        c.update(state=state.get("state", "unclassified"), note=state.get("note", ""), synopsis=synopsis.get(cid, ""))
        c["label"] = c.get("title") or wb._short_id(cid)
        q = c.get("quality")
        c["quality_text"] = f"{q:.4f}" if isinstance(q, (int, float)) and math.isfinite(q) else "—"
        c["representative"] = reps.get(cell) == cid
        c["detail_href"] = f'/exp/{pages._url_segment(snapshot["experiment_name"])}/cell/{pages._url_segment(cell)}' if c["representative"] else ""
        c["raw_href"] = f'/runs/{pages._url_segment(run_id)}/candidates/{pages._url_segment(cid)}/raw' if c.get("log", {}).get("availability") == "present" else ""
        c["availability"] = wb.AVAILABILITY_LABELS.get(c.get("log", {}).get("availability"), "原記録を確認できません")
        c["status"] = statuses.get(cid, {})
        c["output_href"] = output_links.get(cid, "")
        c["synopsis_state"] = "あらすじあり" if c["synopsis"] else {"error":"あらすじ生成に失敗", "unknown":"あらすじ生成の結果不明", "running":"あらすじ生成中", "unreadable":"あらすじを読み込めません"}.get(c["status"].get("synopsize"), "あらすじ未生成")
        c["can_synopsis"] = bool(c.get("screenable") and not running and not output_error and not c["synopsis"] and c["status"].get("synopsize") not in ("ok", "unknown", "running", "pending", "unreadable"))
        target = c.get("ending") or c.get("target_ending")
        c["ending_text"] = endings.get(target, str(target)) if isinstance(target, str) else ("設定した結末に到達" if c.get("reached") else "設定した結末には未到達")
        items.append(c)
    counts = Counter(c["state"] for c in items)
    query = query or {}
    filter_query = {k:v for k,v in query.items() if k not in {"sort", "dir", "candidate", "view_mode", "panel", "q", "config", "run", "kind", "mode", "from_output", "ack", "cell", "publication"}}
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
    categories, bins = data.qd_axes(resolved[2] if resolved else None)
    qd = (config or {}).get("preview", {}).get("qd", {})
    categories = list(qd.get("categories") or categories)
    bins = list(qd.get("volatility_bins") or bins)
    for cell in reps:
        category, _, bin_name = cell.partition("|")
        if category not in categories: categories.append(category)
        if bin_name not in bins: bins.append(bin_name)
    return dict(run_id=run_id, name=snapshot["experiment_name"], label=(config or {}).get("label") or snapshot["experiment_name"],
                world=wb._config_world(config) if config else None, config=config, legacy=legacy,
                revision=selected["revision"], publication=snapshot["revision"], items=items, visible=visible,
                counts=dict(counts), reps=reps, rep_error=rep_error, categories=categories, bins=bins,
                output_error=output_error, running=running, busy=busy, history=history, query=query)
