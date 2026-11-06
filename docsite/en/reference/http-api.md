---
ja_rev: "a0d4d96bc6e6"
---
# HTTP API

!!! warning "Internal API"
    The `/api/...` endpoints listed here are internal APIs used by WorldBloom's own screens (`viewer/`) and can change without notice. They are not a stable API meant for external use. The server only listens on loopback (`127.0.0.1`/`localhost`), and `viewer/job_api.py`'s `boundary()` inspects `Host`/`Origin`/`Sec-Fetch-Site` to reject calls from other origins.

## When it's enabled

- Starting with just `python viewer/server.py --runs <runs>` (no `--control`), there's no `job_store`, so `/api/...` returns `503 unavailable` for **GET as well as POST**. `GET /api/status/local` and `POST /api/status/local/preload`/`POST /api/status/local/unload` do not require `job_store`. Neither does the world route survey, `GET /api/worlds/<id>/routes` (see below).
- Starting with `--control <control>` enables `ConfigStore` and `JobStore`, making the config/job/generation APIs in the table below available.
- The distributed **Viewer exe** (the read-only viewer) rejects every POST outright with `403 "read-only build"`, via `viewer/app_desktop.py`'s `_ReadOnlyHandler.do_POST`, even if `--control` is passed (to prevent writes into `samples/`). The adopt/select checkboxes on screen are still clickable in this build in appearance, but clicking them returning 403 is by design. The **Studio exe** has no such restriction and runs with full functionality.

## Auth and boundary

- Write endpoints (POST) require the `X-WorldBloom-Client: 1` header (missing it returns `403 forbidden`).
- The request body must be `Content-Type: application/json` with exactly one `Content-Length` (no `Transfer-Encoding`).

## Config / job / generation API (`viewer/job_api.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status/local` | GPU and generation status (for the top-bar status dialog). No `job_store` needed |
| POST | `/api/status/local/preload` | Pre-start the local LLM for the current settings |
| POST | `/api/status/local/unload` | Unload the model for the given backend (`llama-server`/`ollama`) |
| GET | `/api/configs` | List saved run settings (configs) |
| GET | `/api/configs/<id>` | Detail of a single run-setting config |
| POST | `/api/configs/preview` | Preview a run config without saving it (scale, ETA, etc.) |
| POST | `/api/configs` | Save a run-setting config |
| POST | `/api/configs/<id>/duplicate` | Duplicate a run-setting config (implemented in `viewer/workbench_pages.py`) |
| GET | `/api/settings/output` | Current generation backend and limits used by ⚙ 全体設定 (Global settings) → AIの設定 (AI settings) |
| POST | `/api/settings/output` | Update text-generation settings (backend/model/limits) |
| POST | `/api/settings/output/api-key` | Save the anthropic/openai API key (write-only; can't be read back) |
| GET | `/api/settings/output/models` | List of selectable models (ollama/llama-server query the real server) |
| POST | `/api/settings/output/test` | Connection test (success is recorded in `verified_models`) |
| GET | `/api/settings/evolution` | Current value of the GA process count |
| POST | `/api/settings/evolution` | Update the GA process count |
| GET | `/api/outputs` | List generation jobs (synopses/full text) |
| GET | `/api/outputs/<id>` | Detail of a single generation job |
| POST | `/api/outputs/<id>/recover` | Recover a generation job that ended abnormally |
| GET | `/api/jobs` | List GA experiment jobs |
| GET | `/api/jobs/<id>` | Detail of a single GA experiment job |
| POST | `/api/jobs` | Submit a GA experiment job |
| POST | `/api/jobs/<id>/cancel` | Stop a GA experiment job |

## World / genre editing API (`viewer/library_pages.py`)

Editing a world/genre is functionally independent of GA experiments. Every handler here except `GET /api/worlds/<id>/routes` goes through `_require_job_store`, so it still needs a `job_store` (i.e. starting with `--control`). There is no `GET /api/worlds` or `GET /api/genres` — the listing is embedded in the screen's HTML.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/worlds` | Create a new world (`mode`: `new`\|`copy`\|`import`; `import` is ZIP import) |
| POST | `/api/worlds/<id>/edit` | Edit a world (the screen's "世界を編集", Edit world; `operation: genre` also picks/changes the genre) |
| POST | `/api/worlds/<id>/basics` | Save only a world's basic info |
| POST | `/api/worlds/<id>/files` | Save a YAML file under a world |
| POST | `/api/worlds/<id>/validate` | Validate a world's YAML (doesn't save) |
| POST | `/api/genres` | Create a new genre |
| POST | `/api/genres/<id>/files` | Save a YAML file under a genre |
| POST | `/api/genres/<id>/{save,parse,check}` | Save / parse / validate in the genre editor |
| POST | `/api/worlds/<id>/patches/<patch_id>/{approve,reject}` | Approve / reject a world self-expansion patch |
| POST | `/api/worlds/<id>/patches/reopen` | Revert an approved expansion patch back to proposed |
| GET | `/api/worlds/<id>/routes` | [Survey shortest routes](../concepts/route-layer.md#route-paths-survey) (read-only). Unlike every other row in this table, it does not need `job_store` (`--control`), and works in the read-only Viewer too. `404 not_found` if the world doesn't exist. `status: "no_route_config"` if the world's genre isn't set or has no `route.yaml`; `status: "no_goal"` if the protagonist's goal isn't a supported shape. Response shape: `{"status": "ok"\|"unreachable"\|"no_goal"\|"no_route_config", "message": str, "timepoints": [{"label", "day", "slot", "events": [...], "start": {...}, "routes": [{"rank", "label", "h", "cost", "route", "conditions", "signature", "steps", "truncated"}], "blocked": [{"kind", "value", "detail", "text"}]}]}` |

## Run-results and selection API

| Method | Path | Purpose |
|---|---|---|
| POST | `/exp/<experiment>/selection` | Update adopt/hold/exclude selection state. No `X-WorldBloom-Client` header needed, and it works without a `job_store`; it's only rejected via `assert_run_idle` when a `job_store` exists and that run is in progress |
| POST | `/exp/<experiment>/delete` | Delete an experiment (the whole `runs/<experiment>/`). Requires `job_store` |
| POST | `/exp/<experiment>/cell/<cell>/reader-summary` | Generate a reader-facing summary for one cell |
| GET / POST | `/api/runs/<rid>/selection` | Get / update one run's selection state (`viewer/run_catalog.py`) |
| POST | `/api/runs/<rid>/world-patch` | Propose or check a world self-expansion patch (`propose`/`check`; requires `job_store`, `viewer/run_catalog.py`) |

There's more beyond this — `viewer/run_catalog.py` (`/api/runs`, `/api/selected`) and `viewer/workbench_pages.py` (mostly GET routes for screen display) — but these are all internal APIs matching UI operations on screen. See each file's `dispatch()`/`_resolve()` for the exact list.

## World drafts, refinements, and world conditions

These internal APIs are also handled by `viewer/library_pages.py`. Updating a draft and adopting it as a world are separate operations.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/world-draft-generation` | World-generation availability under current settings |
| POST | `/api/world-drafts` | Request world-draft generation |
| GET | `/api/world-drafts/<token>` | Draft state and content |
| POST | `/api/world-drafts/<token>/{stage,cancel,validate,adopt}` | Stage edited content, cancel, validate, or adopt a draft as a world |
| POST | `/api/world-drafts/<token>/refine` | Create a draft-refinement candidate |
| GET | `/api/world-drafts/<token>/refinements/<request_id>` | Get a candidate (`capabilities` lists supported operations) |
| POST | `/api/world-drafts/<token>/{apply-refinement,cancel-refinement,save-refinement-input}` | Apply/cancel a candidate or save input |
| GET | `/api/worlds/<id>/action-usage` | Read action and mechanics settings |
| POST | `/api/worlds/<id>/action-usage/{check,save}` | Validate/save action and mechanics settings |
| GET / POST | `/api/worlds/<id>/route-goal` | Read/save goal settings |
| POST | `/api/worlds/<id>/route-goal/validate` | Validate goal settings |

Refinement requests carry `revision`, `request_id`, `mode`, `prompt`, `preset`, and `targets`. Applying a candidate requires `revision`, `request_id`, `candidate_id`, `candidate_hash`, and `acknowledged_warnings`, preventing overwrites of stale drafts. A refinement supports at most 16 operations, with neither partial application nor automatic publication as a world. Supported areas include character descriptions, places, routes, items, schedules, and action constraints. Adding characters, changing goals, renaming/deleting entities, and adding forced actions are unsupported.
