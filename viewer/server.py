"""Dependency-free HTTP viewer for WorldBloom experiment runs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from viewer import data, pages, job_api, library_pages, run_catalog, workbench_pages, output_pages
from execution.configs import ConfigStore
from execution.jobs import JobStore
from execution.provenance import ConfigError
from viewer.data import (
    BadRequest,
    ForbiddenPath,
    MissingResource,
    RunRepository,
)


MAX_POST_BYTES = 64 * 1024
STATIC_FILES = {
    "world-create.css": "text/css; charset=utf-8",
    "genre-workspace.css": "text/css; charset=utf-8",
    "genre-workspace.js": "application/javascript; charset=utf-8",
    "world-create.js": "application/javascript; charset=utf-8",
    "global-settings.css": "text/css; charset=utf-8",
    "global-settings.js": "application/javascript; charset=utf-8",
    "generation.css": "text/css; charset=utf-8",
    "generation.js": "application/javascript; charset=utf-8",
    "screening-workspace.css": "text/css; charset=utf-8",
    "screening-workspace.js": "application/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "workbench.js": "text/javascript; charset=utf-8",
    "run-workspace.css": "text/css; charset=utf-8",
    "sifting-workspace.css": "text/css; charset=utf-8",
    "sifting-workspace.js": "application/javascript; charset=utf-8",
    "raw-workspace.css": "text/css; charset=utf-8",
    "raw-workspace.js": "application/javascript; charset=utf-8",
    "lineage-workspace.css": "text/css; charset=utf-8",
    "lineage-workspace.js": "application/javascript; charset=utf-8",
    "comparison.css": "text/css; charset=utf-8",
    "comparison.js": "application/javascript; charset=utf-8",
    "run-settings.css": "text/css; charset=utf-8",
    "run-settings.js": "application/javascript; charset=utf-8",
    "run-workspace.js": "text/javascript; charset=utf-8",
    "ga_replay.js": "text/javascript; charset=utf-8",
    "world-prototype.css": "text/css; charset=utf-8",
    "world-prototype.js": "text/javascript; charset=utf-8",
}


def static_path(name: str) -> Path:
    """Resolve one explicitly allowed static asset."""

    data.RunRepository.validate_segment(name)
    if name not in STATIC_FILES:
        raise MissingResource(f"static file not found: {name}")
    path = (Path(__file__).resolve().parent / "static" / name).resolve()
    static_root = (Path(__file__).resolve().parent / "static").resolve()
    try:
        path.relative_to(static_root)
    except ValueError as error:
        raise ForbiddenPath("static path leaves viewer/static") from error
    if not path.is_file():
        raise MissingResource(f"static file not found: {name}")
    return path


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    _last_reconcile = 0.0

    def service_actions(self):
        jobs = getattr(self, "job_store", None)
        if jobs is None:
            return
        now = time.monotonic()
        if now - self._last_reconcile < 2.0:
            return
        self._last_reconcile = now
        try:
            jobs.list()
        except (ConfigError, OSError, ValueError):
            pass


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "WorldBloomViewer/2.0"

    @property
    def repository(self) -> RunRepository:
        repository = getattr(self.server, "repository", None)
        if not isinstance(repository, RunRepository):
            raise RuntimeError("viewer repository is not configured")
        return repository

    def _parts(self) -> list[str]:
        raw_path = urlsplit(self.path).path
        decoded = [unquote(part) for part in raw_path.split("/")]
        for part in decoded:
            if (
                part in {".", ".."}
                or "/" in part
                or "\\" in part
                or "\x00" in part
            ):
                raise ForbiddenPath("path traversal")
        return [part for part in decoded if part]

    def _send_bytes(
        self,
        status: HTTPStatus,
        content_type: str,
        payload: bytes,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, document: str) -> None:
        self._send_bytes(
            HTTPStatus.OK,
            "text/html; charset=utf-8",
            document.encode("utf-8"),
        )

    def _send_json(
        self,
        status: HTTPStatus,
        value: Mapping[str, Any],
    ) -> None:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            payload,
        )

    def _dispatch_get(self) -> None:
        parts = self._parts()
        if output_pages.dispatch(self, parts, "GET"):
            return
        if workbench_pages.dispatch(self, parts, "GET"):
            return
        if library_pages.dispatch(self, parts, "GET"):
            return
        if run_catalog.dispatch(self, parts, "GET"):
            return
        if job_api.dispatch(self, parts, "GET"):
            return
        job_store = getattr(self.server, "job_store", None)
        if not parts:
            self._send_html(pages.index_page(self.repository, job_store=job_store))
            return
        if len(parts) == 2 and parts[0] == "static":
            name = parts[1]
            payload = static_path(name).read_bytes()
            self._send_bytes(
                HTTPStatus.OK,
                STATIC_FILES[name],
                payload,
            )
            return
        if len(parts) == 3 and parts[0] == "exp" and parts[2] == "monitor":
            from viewer import run_workspace
            run_workspace.experiment_page(self, parts[1])
            return
        if len(parts) == 3 and parts[0] == "exp" and parts[2] == "river":
            from viewer import lineage_river
            query = parse_qs(urlsplit(self.path).query)
            html = lineage_river.river_page(self.repository, parts[1], selected_cell=query.get("cell", [None])[0], job_store=job_store)
            self._send_html(html.replace("</head>", '<link rel="stylesheet" href="/static/run-workspace.css"></head>'))
            return
        if len(parts) == 3 and parts[0] == "exp" and parts[2] == "compare":
            query = parse_qs(urlsplit(self.path).query)
            if self.repository.catalog is not None:
                from viewer import compare_pages
                compare_pages.render(self, parts[1], query)
                return
            if "publication" in query or "candidate" in query:
                catalog = self.repository.catalog
                if catalog is None:
                    raise BadRequest("比較対象の公開版を確認できません")
                snapshot = catalog.snapshot(catalog.run_id(parts[1]))
                cells, ids = query.get("cell", []), query.get("candidate", [])
                reps = catalog.representatives(snapshot)
                if (query.get("publication") != [str(snapshot["revision"])]
                        or not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids)
                        or len(cells) != len(ids)
                        or any(reps.get(cell) != cid for cell, cid in zip(cells, ids))):
                    raise BadRequest("比較対象が更新されています。格子で対象を選び直してください")
            self._send_html(pages.compare_page(
                self.repository, parts[1], query.get("cell", []), job_store=job_store,
            ))
            return
        if len(parts) == 5 and parts[0] == "exp" and parts[2] == "cell" and parts[4] == "raw":
            query = parse_qs(urlsplit(self.path).query)
            self._send_html(pages.raw_page(
                self.repository, parts[1], parts[3], query.get("line", [None])[0],
                job_store=job_store, q=query.get("q", [""])[0],
                kind=query.get("kind", [""])[0], person=query.get("person", [""])[0],
                page=query.get("page", [None])[0], mode=query.get("mode", ["readable"])[0],
                expected_source=query.get("source", [None])[0],
            ))
            return
        if len(parts) == 5 and parts[0] == "exp" and parts[2] == "cell" and parts[4] == "lineage":
            query = parse_qs(urlsplit(self.path).query)
            raw_turning = query.get("turning", [None])[0]
            turning_index = None
            if raw_turning is not None:
                try:
                    turning_index = int(raw_turning)
                except ValueError:
                    turning_index = None
            self._send_html(pages.lineage_page(
                self.repository, parts[1], parts[3], turning_index=turning_index,
                job_store=job_store, point=query.get("point", [None])[0],
                tab=query.get("tab", ["choices"])[0],
                view=query.get("view", ["key"])[0],
                expected_ref=query.get("elite", [None])[0],
            ))
            return
        if len(parts) == 2 and parts[0] == "exp":
            if self.repository.catalog is not None:
                from viewer import sifting_pages
                rid = self.repository.catalog.run_id(parts[1])
                if rid.startswith("legacy-"):
                    rid = self.repository.catalog.register_legacy(parts[1])
                sifting_pages.candidates(self, rid, grid=True)
                return
            self._send_html(
                pages.experiment_page(
                    self.repository,
                    parts[1],
                    job_store=job_store,
                )
            )
            return
        if (
            len(parts) == 4
            and parts[0] == "exp"
            and parts[2] == "cell"
        ):
            query = parse_qs(urlsplit(self.path).query)
            values = query.get("view", ["digest"])
            view = values[0]
            self._send_html(
                pages.cell_page(
                    self.repository,
                    parts[1],
                    parts[3],
                    view=view,
                    job_store=job_store,
                )
            )
            return
        raise MissingResource("route not found")

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except ConfigError as error:
            job_api.send_error(self, error)
        except ForbiddenPath:
            self.send_error(HTTPStatus.FORBIDDEN.value)
        except MissingResource:
            self.send_error(HTTPStatus.NOT_FOUND.value)
        except BadRequest as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error)},
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            self.send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR.value,
                "Could not read experiment artifacts",
            )

    def _request_json(self) -> Mapping[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError as error:
            raise BadRequest("invalid Content-Length") from error
        if length < 1:
            raise BadRequest("empty request body")
        if length > MAX_POST_BYTES:
            raise BadRequest("request body is too large")

        try:
            value = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BadRequest("invalid JSON") from error
        if not isinstance(value, Mapping):
            raise BadRequest("JSON root must be an object")
        return value

    def _update_selection(
        self,
        experiment_name: str,
    ) -> None:
        experiment = self.repository.experiment(experiment_name)
        archive = self.repository.archive(experiment)
        valid_cells = set(archive["cells"])

        body = self._request_json()
        cell = body.get("cell")
        selected_value = body.get("selected")
        if not isinstance(cell, str):
            raise BadRequest("cell must be a string")
        self.repository.validate_segment(cell)
        if cell not in valid_cells:
            raise BadRequest("cell is not present in archive")
        if not isinstance(selected_value, bool):
            raise BadRequest("selected must be a boolean")

        selected = self.repository.toggle_selection(experiment, cell, selected_value)
        self._send_json(
            HTTPStatus.OK,
            {
                "cell": cell,
                "selected": selected_value,
                "selected_cells": sorted(selected),
            },
        )

    def _generate_reader_summary(
        self,
        experiment_name: str,
        cell: str,
    ) -> None:
        # WB-EXPLAIN-009: on-demand reader prose for one candidate. Read
        # only (no request body); only ever writes into
        # reader-summaries/, never into archive.json/selection/layers.jsonl.
        jobs = getattr(self.server, "job_store", None)
        settings_path = getattr(self.server, "settings_path", None)
        if jobs is None or settings_path is None:
            raise MissingResource("route not found")
        job_api.boundary(self, client_header=True, body_required=False)
        from execution.output_settings import resolve_generation

        generation = resolve_generation(settings_path)
        if generation["backend"] == "none":
            raise ConfigError("generation", "文章生成のバックエンドが未設定です", code="unavailable")
        # The call can hold reader-summaries/'s directory_lock for minutes;
        # a concurrent GA job publishing a generation or a selection write
        # takes the same lock non-blocking (execution/evolution_worker.py,
        # execution/selections.py), so refuse while a run is active rather
        # than risk starving it.
        jobs.assert_run_idle(experiment_name)
        experiment = self.repository.experiment(experiment_name)
        self.repository.validate_segment(cell)
        summary = data.ensure_reader_summary(
            self.repository, experiment, cell,
            settings_path=settings_path, backend=generation["backend"],
            timeout=generation["limits"]["call_timeout_seconds"],
        )
        self._send_json(HTTPStatus.OK, {"cell": cell, "reviewed": summary["reviewed"]})

    def do_POST(self) -> None:
        try:
            parts = self._parts()
            if output_pages.dispatch(self, parts, "POST"):
                return
            if workbench_pages.dispatch(self, parts, "POST"):
                return
            if library_pages.dispatch(self, parts, "POST"):
                return
            if run_catalog.dispatch(self, parts, "POST"):
                return
            if job_api.dispatch(self, parts, "POST"):
                return
            if (
                len(parts) == 3
                and parts[0] == "exp"
                and parts[2] == "selection"
            ):
                job_api.boundary(self, client_header=False)
                jobs = getattr(self.server, "job_store", None)
                if jobs is not None:
                    jobs.assert_run_idle(parts[1])
                self._update_selection(parts[1])
                return
            if len(parts) == 3 and parts[0] == "exp" and parts[2] == "delete":
                jobs = getattr(self.server, "job_store", None)
                if jobs is None:
                    raise MissingResource("route not found")
                job_api.boundary(self, client_header=True, body_required=False)
                self.repository.validate_segment(parts[1])
                self._send_json(HTTPStatus.OK, jobs.delete_run(parts[1]))
                return
            if (
                len(parts) == 5
                and parts[0] == "exp"
                and parts[2] == "cell"
                and parts[4] == "reader-summary"
            ):
                self._generate_reader_summary(parts[1], parts[3])
                return
            raise MissingResource("route not found")
        except ConfigError as error:
            job_api.send_error(self, error)
        except ForbiddenPath:
            self.send_error(HTTPStatus.FORBIDDEN.value)
        except MissingResource:
            self.send_error(HTTPStatus.NOT_FOUND.value)
        except BadRequest as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error)},
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            self.send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR.value,
                "Could not update selection",
            )

    def log_message(
        self,
        format: str,
        *args: Any,
    ) -> None:
        print(
            f"{self.address_string()} "
            f"[{self.log_date_time_string()}] "
            f"{format % args}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="View WorldBloom experiment archives.",
    )
    parser.add_argument(
        "--runs",
        type=Path,
        required=True,
        help="Root containing WorldBloom experiment directories.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Listen address; defaults to loopback only.",
    )
    parser.add_argument(
        "--port",
        type=int,
        # PORT lets a launcher that dynamically assigns ports (e.g. to dodge
        # a collision with another session already on 5401) pick the port
        # without a hardcoded --port flag in launch.json.
        default=int(os.environ.get("PORT", 5401)),
    )
    parser.add_argument("--control", type=Path, help="Enable persistent configuration/job APIs at this control root.")
    parser.add_argument("--repo", type=Path, default=ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        raise ValueError("--port must be between 0 and 65535")

    repository = RunRepository(args.runs)
    server = ViewerServer(
        (args.host, args.port),
        ViewerHandler,
    )
    server.repository = repository
    if args.control is not None:
        if not job_api.loopback(args.host):
            server.server_close()
            raise ValueError("execution API requires a loopback host")
        server.job_store = JobStore(ConfigStore(args.repo, args.control, args.runs))
        server.settings_path = args.repo / "settings.json"
        server.repository = RunRepository(args.runs, control_root=args.control, jobs=server.job_store)

    host, port = server.server_address[:2]
    print(
        f"WorldBloom viewer: http://{host}:{port}/ "
        f"(runs={repository.runs_root})",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
