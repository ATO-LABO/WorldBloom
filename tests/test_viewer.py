"""End-to-end tests for the dependency-free WorldBloom viewer."""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "viewer" / "server.py"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_page(url: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                return response.read().decode("utf-8")
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.05)
    raise AssertionError(
        f"viewer did not become ready: {last_error}"
    )


def _wait_for_port_release(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM,
            ) as listener:
                listener.bind(("127.0.0.1", port))
                return
        except OSError as error:
            last_error = error
            time.sleep(0.05)
    raise AssertionError(
        f"viewer port {port} was not released: {last_error}"
    )


def _fixture_rows() -> list[dict[str, Any]]:
    return [
        {
            "antagonist": "鬼",
            "engine_hash": "engine",
            "genome": None,
            "kind": "header",
            "precedent_hash": "precedent",
            "protagonist": "桃太郎",
            "seed": 7,
            "world": "桃太郎",
        },
        {
            "args": ["鬼ヶ島"],
            "classification": {
                "category": "III",
                "target_role": "hostile",
            },
            "day": 1,
            "delta": {
                "actor": {"zone": "鬼ヶ島"},
                "objective": None,
                "relations": [],
                "targets": {},
            },
            "effective": True,
            "kind": "decision",
            "result": "moved",
            "slot": "morning",
            "subject": "桃太郎",
            "turn": 1,
            "verb": "move",
        },
        {
            "day": 1,
            "kind": "snapshot",
            "layers": {
                "ability": {"base": 50.0, "modifiers": []},
                "belief": {},
                "identity": {
                    "displayed": "桃太郎",
                    "true": "桃太郎",
                },
                "objective": {"宝物": "鬼"},
                "pending": [],
                "phase": [],
                "resources": {
                    "assets": {},
                    "bonds": 0.1,
                    "reputation": 0.0,
                },
                "vitality": "alive",
                "zone": "鬼ヶ島",
            },
            "relations": [],
            "subject": "桃太郎",
            "turn": 1,
            "vector": [
                0.5,
                0.0,
                0.1,
                0.1,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
            ],
        },
        {
            "args": ["鬼"],
            "classification": {
                "category": "III",
                "target_role": "hostile",
            },
            "day": 1,
            "delta": {
                "actor": {},
                "objective": {"宝物": "桃太郎"},
                "relations": [],
                "targets": {"鬼": {"vitality": "downed"}},
            },
            "effective": True,
            "kind": "decision",
            "result": "won",
            "slot": "evening",
            "subject": "桃太郎",
            "turn": 2,
            "verb": "fight",
        },
        {
            "day": 1,
            "details": {"label": "凱旋"},
            "id": "homecoming",
            "kind": "event",
            "result": "applied",
            "slot": "evening",
            "subject": "桃太郎",
            "turn": 2,
            "verb": "ending",
        },
        {
            "day": 1,
            "kind": "snapshot",
            "layers": {
                "ability": {"base": 50.0, "modifiers": []},
                "belief": {},
                "identity": {
                    "displayed": "桃太郎",
                    "true": "桃太郎",
                },
                "objective": {"宝物": "桃太郎"},
                "pending": [],
                "phase": ["homecoming"],
                "resources": {
                    "assets": {"宝物": 1},
                    "bonds": 0.5,
                    "reputation": 0.3,
                },
                "vitality": "alive",
                "zone": "村",
            },
            "relations": [],
            "subject": "桃太郎",
            "turn": 2,
            "vector": [
                0.5,
                0.0,
                0.5,
                0.2,
                0.1,
                0.3,
                1.0,
                1.0,
                0.0,
                1.0,
                1.0,
            ],
        },
    ]


def _create_experiment(runs_root: Path) -> Path:
    experiment = runs_root / "exp-viewer"
    layers_path = (
        experiment
        / "g0"
        / "ind-0"
        / "seed-7"
        / "layers.jsonl"
    )
    _write_jsonl(layers_path, _fixture_rows())

    elite = {
        "descriptor": {
            "category": "III",
            "volatility": 0.25,
            "volatility_bin": "high",
        },
        "exemplar": {
            "engine_hash": "engine",
            "layers_path": "g0/ind-0/seed-7/layers.jsonl",
            "precedent_hash": "precedent",
            "seed": 7,
        },
        "generation": 0,
        "genome": {
            "category_weight": {
                "I": 0.5,
                "II": 0.5,
                "III": 0.5,
                "IV": 0.5,
                "V": 0.5,
                "VI": 0.5,
            },
            "novelty_drive": 0.0,
            "risk_tolerance": 0.5,
            "stance_shift_bias": 0.0,
        },
        "parents": [],
        "quality": 0.75,
        "reach_rate": 1.0,
    }
    _write_json(
        experiment / "archive.json",
        {
            "cells": {"III|high": elite},
            "volatility_thresholds": {
                "low_max": 0.05,
                "mid_max": 0.15,
            },
        },
    )
    _write_json(
        experiment / "synopses.json",
        {
            "archive": "archive.json",
            "backend": "none",
            "entries": [
                {
                    "cell": "III|high",
                    "descriptor": elite["descriptor"],
                    "generation": 0,
                    "layers_path": elite["exemplar"]["layers_path"],
                    "prompt_path": "prompts/synopsis-III-high.txt",
                    "quality": 0.75,
                    "reach_rate": 1.0,
                    "seed": 7,
                    "status": "ok",
                    "synopsis": "桃太郎は鬼を退け、宝物を村へ持ち帰った。",
                }
            ],
            "world": "桃太郎",
        },
    )
    story = experiment / "stories" / "III-high.md"
    story.parent.mkdir(parents=True, exist_ok=True)
    story.write_text(
        "# 桃太郎の凱旋\n\n桃太郎は鬼ヶ島へ向かった。\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_json(
        experiment / "stories" / "index.json",
        {
            "archive": "archive.json",
            "backend": "none",
            "entries": [
                {
                    "cell": "III|high",
                    "status": "ok",
                    "story_path": "III-high.md",
                }
            ],
            "selected": ["III|high"],
            "world": "桃太郎",
        },
    )
    return experiment


class ViewerServerTests(unittest.TestCase):
    def test_server_start_fetch_update_stop_and_release_port(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            experiment = _create_experiment(runs_root)
            port = _available_port()
            base_url = f"http://127.0.0.1:{port}"

            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            creationflags = (
                subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32"
                else 0
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SERVER),
                    "--runs",
                    str(runs_root),
                    "--port",
                    str(port),
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                creationflags=creationflags,
            )

            try:
                index = _wait_for_page(base_url + "/")
                self.assertIn("世界を選ぶ", index)
                self.assertIn("exp-viewer", index)

                grid = _wait_for_page(base_url + "/exp/exp-viewer")
                self.assertIn("III|high", grid)
                self.assertIn("0.7500", grid)
                self.assertIn("100.0%", grid)

                for query in ("", "?cell=III%7Chigh"):
                    with urllib.request.urlopen(base_url + "/exp/exp-viewer/compare" + query, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get_content_type(), "text/html")
                        self.assertIn("異なる候補を2〜4件", response.read().decode("utf-8"))

                cell = _wait_for_page(
                    base_url + "/exp/exp-viewer/cell/III%7Chigh"
                )
                self.assertIn("7層の推移", cell)
                self.assertIn("<svg", cell)
                self.assertIn("模範ランのターン列", cell)
                self.assertIn(
                    "桃太郎は鬼を退け、宝物を村へ持ち帰った。",
                    cell,
                )
                self.assertIn("桃太郎の凱旋", cell)

                request = urllib.request.Request(
                    base_url + "/exp/exp-viewer/selection",
                    data=json.dumps(
                        {
                            "cell": "III|high",
                            "selected": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(
                    request,
                    timeout=2.0,
                ) as response:
                    result = json.loads(
                        response.read().decode("utf-8")
                    )
                self.assertTrue(result["selected"])
                self.assertEqual(
                    json.loads(
                        (experiment / "selection.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                    {"selected": ["III|high"]},
                )

                connection = http.client.HTTPConnection(
                    "127.0.0.1",
                    port,
                    timeout=2.0,
                )
                try:
                    connection.request("GET", "/exp/%2e%2e")
                    response = connection.getresponse()
                    response.read()
                    self.assertEqual(response.status, 403)
                finally:
                    connection.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)

            _wait_for_port_release(port)
            self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()