---
ja_rev: "aae94c852743"
---
# HTTP API

!!! warning "Internal API"
    The `/api/...` endpoints listed here are internal APIs used by WorldBloom's own screens (`viewer/`) and can change without notice. They are not a stable API meant for external use. The server only listens on loopback (`127.0.0.1`/`localhost`), and `viewer/job_api.py`'s `boundary()` inspects `Host`/`Origin`/`Sec-Fetch-Site` to reject calls from other origins.

## When it's enabled

- Starting with just `python viewer/server.py --runs <runs>`, the write endpoints under `/api/...` **can't be used** (there's no `job_store`, so they return `503 unavailable`). Only the read-only `GET /api/status/local` always works.
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
| POST | `/api/configs/<id>/duplicate` | Duplicate a run-setting config |
| GET | `/api/settings/output` | Current values on the ⚙ Settings "文章生成" (Text generation) tab |
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

Works with `--control` regardless of whether `job_store` is present (editing a world/genre is independent of GA experiments).

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/worlds` | List worlds / create a new one |
| POST | `/api/worlds/<id>/edit` | Edit a world (the screen's "世界を編集", Edit world) |
| POST | `/api/worlds/<id>/basics` | Save only a world's basic info |
| POST | `/api/worlds/<id>/files` | Save a YAML file under a world |
| POST | `/api/worlds/<id>/validate` | Validate a world's YAML (doesn't save) |
| GET / POST | `/api/genres` | List genres / create a new one |
| POST | `/api/genres/<id>/files` | Save a YAML file under a genre |
| POST | `/api/genres/<id>/{save,parse,check}` | Save / parse / validate in the genre editor |
| POST | `/api/worlds/<id>/patches/<patch_id>/{approve,reject}` | Approve / reject a world self-expansion patch |
| POST | `/api/worlds/<id>/patches/reopen` | Revert an approved expansion patch back to proposed |

## Run-results and selection API

| Method | Path | Purpose |
|---|---|---|
| POST | `/exp/<experiment>/selection` | Update adopt/hold/exclude selection state (this path works even without a `job_store`, unlike the read-only viewer's other routes; it's rejected via `assert_run_idle` while that run is in progress) |
| POST | `/exp/<experiment>/delete` | Delete an experiment (the whole `runs/<experiment>/`). Requires `job_store` |
| POST | `/exp/<experiment>/cell/<cell>/reader-summary` | Generate a reader-facing summary for one cell |

There's more beyond this — `viewer/run_catalog.py` (`/api/runs`, `/api/selected`) and `viewer/workbench_pages.py` (mostly GET routes for screen display, aside from `/api/configs/<id>/duplicate`) — but these are all internal APIs matching UI operations on screen. See each file's `dispatch()`/`_resolve()` for the exact list.
