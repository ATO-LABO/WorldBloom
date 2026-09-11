"""World metadata, deterministic prompts, and standard-library LLM backends."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from gapengine.qd import Elite


BACKENDS = (
    "claude-cli",
    "codex-cli",
    "anthropic",
    "openai",
    "none",
)


@dataclass(frozen=True)
class GenerationResult:
    status: str
    text: str | None = None
    warning: str | None = None


class GenerationError(RuntimeError):
    """One recoverable LLM generation failure."""


def _validate_response(text: str) -> str:
    """Reject empty output and short clarification requests."""

    cleaned = text.strip()
    if not cleaned:
        raise GenerationError("clarification_request")

    lines = [
        line.strip()
        for line in cleaned.splitlines()
        if line.strip()
    ]
    clarification_phrases = (
        "教えてください",
        "確認させてください",
    )
    looks_like_clarification = (
        1 <= len(lines) <= 3
        and (
            any(
                phrase in cleaned
                for phrase in clarification_phrases
            )
            or cleaned.endswith(("?", "？"))
        )
    )
    if looks_like_clarification:
        raise GenerationError("clarification_request")

    return cleaned


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _yaml_mapping(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return value


def load_world_meta(
    project_dir: str | Path,
    template_dir: str | Path,
) -> dict[str, Any]:
    """Load only descriptive data needed by output prompts."""

    project = Path(project_dir)
    template = Path(template_dir)
    world = _yaml_mapping(project / "world.yaml")

    display_names: dict[str, str] = {}
    subject_traits: dict[str, dict[str, float]] = {}
    initial_modifiers: dict[str, list[dict[str, Any]]] = {}
    identities: list[dict[str, str]] = []

    subjects_dir = project / "subjects"
    for path in sorted(subjects_dir.glob("*.yaml"), key=lambda item: item.name):
        subject = _yaml_mapping(path)
        subject_id = str(subject["id"])
        identity = _as_mapping(subject.get("identity"))
        displayed = str(identity.get("displayed", subject_id))
        display_names[subject_id] = displayed
        identities.append(
            {
                "id": subject_id,
                "displayed": displayed,
            }
        )
        subject_traits[subject_id] = {
            str(key): float(value)
            for key, value in sorted(
                _as_mapping(subject.get("traits")).items()
            )
        }

        modifiers: list[dict[str, Any]] = []
        for raw in subject.get("modifiers", []) or []:
            if isinstance(raw, Mapping):
                modifiers.append(dict(raw))
        inventory = _as_mapping(subject.get("inventory"))
        item_definitions = {
            str(item["name"]): item
            for item in world.get("items", []) or []
            if isinstance(item, Mapping) and "name" in item
        }
        for item_name, count in sorted(inventory.items()):
            if int(count) <= 0:
                continue
            item = _as_mapping(item_definitions.get(str(item_name)))
            modifier = item.get("modifier")
            if isinstance(modifier, Mapping):
                modifiers.append(
                    {
                        **dict(modifier),
                        "source": str(item_name),
                    }
                )
        initial_modifiers[subject_id] = modifiers

    effects_path = template / "effects.yaml"
    effects_raw: Any = []
    if effects_path.is_file():
        effects_raw = yaml.safe_load(
            effects_path.read_text(encoding="utf-8")
        )
    effect_descriptions = {
        str(effect["id"]): str(
            _as_mapping(effect.get("payoff")).get("description", "")
        )
        for effect in effects_raw or []
        if isinstance(effect, Mapping) and "id" in effect
    }

    endings = [
        ending
        for ending in world.get("ending", []) or []
        if isinstance(ending, Mapping)
    ]
    ending_labels = {
        str(ending["id"]): str(ending.get("label", ending["id"]))
        for ending in endings
    }
    target_ending = str(world.get("target_ending", ""))
    protagonist = str(world.get("protagonist", ""))
    antagonist = str(world.get("antagonist", ""))

    return {
        "name": str(world.get("name", project.name)),
        "protagonist": protagonist,
        "antagonist": antagonist,
        "display_names": display_names,
        "identities": identities,
        "target_ending": target_ending,
        "target_ending_label": ending_labels.get(
            target_ending,
            target_ending,
        ),
        "ending_labels": ending_labels,
        "effect_descriptions": effect_descriptions,
        "protagonist_traits": subject_traits.get(protagonist, {}),
        "contest_epsilon": float(
            _as_mapping(world.get("contest")).get("epsilon", 0.0)
        ),
        "default_strength_prior": float(
            world.get("default_strength_prior", 0.0)
        ),
        "initial_modifiers": initial_modifiers,
    }


def _elite_dict(elite: Elite | Mapping[str, Any]) -> Mapping[str, Any]:
    return elite.to_dict() if isinstance(elite, Elite) else elite


def _state_text(state: Mapping[str, Any]) -> str:
    holders = _as_mapping(state.get("holder"))
    holder_text = "、".join(
        f"{item}は{holder if holder is not None else '誰も所持していない'}"
        for item, holder in sorted(holders.items())
    ) or "目的物の所持情報なし"
    phase = "、".join(str(value) for value in state.get("phase", []))
    believed = state.get("believed_strength")
    believed_text = "不明" if believed is None else str(believed)
    stance = state.get("stance")
    stance_text = "不明" if stance is None else str(stance)

    return (
        f"主人公の強さ {state.get('strength', '不明')}、"
        f"主人公が見積もる敵役の強さ {believed_text}、"
        f"主人公から敵役への感情値 {stance_text}、"
        f"{holder_text}、"
        f"段階 {phase or 'なし'}、"
        f"生命状態 {state.get('vitality', '不明')}、"
        f"場所 {state.get('zone', '不明')}"
    )


def _scene_lines(
    scenes: Sequence[Mapping[str, Any]],
    *,
    detailed: bool,
) -> list[str]:
    lines: list[str] = []
    for scene in scenes:
        slot = scene.get("slot")
        when = f"第{scene.get('day', 0)}日"
        if slot is not None:
            when += f"・{slot}"
        events = "／".join(
            str(value) for value in scene.get("events", [])
        )
        lines.append(
            f"- ターン{scene.get('turn')}（{when}）: {events}"
        )
        lines.append(
            f"  その時点の状態: "
            f"{_state_text(_as_mapping(scene.get('state')))}"
        )
        if detailed:
            reasons = "、".join(
                str(value) for value in scene.get("reasons", [])
            )
            lines.append(
                f"  転機として残した理由: {reasons or '状態変化'}"
                f"。層変化量は {scene.get('delta_l1', 0.0)}"
            )
        foreshadowing = scene.get("foreshadowing")
        if isinstance(foreshadowing, list) and foreshadowing:
            lines.append(
                "  伏線: "
                + "／".join(str(value) for value in foreshadowing)
            )
    return lines


def _character_text(world_meta: Mapping[str, Any]) -> str:
    identities = world_meta.get("identities")
    if not isinstance(identities, list):
        return "（人物情報なし）"
    return "、".join(
        str(_as_mapping(identity).get("displayed", "不明"))
        for identity in identities
    )


def build_synopsis_prompt(
    elite: Elite | Mapping[str, Any],
    scenes: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
) -> str:
    """Build a byte-stable synopsis prompt containing no predicate syntax."""

    raw = _elite_dict(elite)
    cell = str(raw.get("cell", "不明"))
    scene_text = "\n".join(_scene_lines(scenes, detailed=False))

    return (
        "あなたは物語のあらすじ作家です。質問や確認を返さず、"
        "以下の情報だけから指定の文字数であらすじ本文のみを"
        "出力してください。\n\n"
        "# WorldBloom あらすじ作成依頼\n\n"
        "以下はシミュレーションから抽出した物語上の事実です。"
        "記号的な設定名を列挙せず、出来事の因果が伝わる自然な日本語に"
        "まとめてください。\n\n"
        f"- 世界: {world_meta.get('name', '不明')}\n"
        f"- 登場人物: {_character_text(world_meta)}\n"
        f"- 固定された結末: "
        f"{world_meta.get('target_ending_label', '不明')}\n"
        f"- アーカイブ上の位置: {cell}\n"
        f"- 品質: {raw.get('quality', '不明')}\n"
        f"- 結末到達率: {raw.get('reach_rate', '不明')}\n\n"
        "## 注目ターン\n"
        f"{scene_text if scene_text else '- 注目ターンなし'}\n\n"
        "## 指示\n"
        "- 200〜300字の日本語のあらすじを一段落で書く。\n"
        "- 固定された結末を明かす。\n"
        "- 導入の説明より、道中の転機、選択、逆転を中心にする。\n"
        "- ログにない人物、勝敗、所持品、因果を追加しない。\n\n"
        "## 出力形式\n"
        "あらすじ本文のみを出力する。見出し、箇条書き、前置き、"
        "質問、確認は出力しない。\n"
    )


def build_narration_prompt(
    elite: Elite | Mapping[str, Any],
    scenes: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
    *,
    synopsis: str | None = None,
) -> str:
    """Build a byte-stable long-form narration prompt."""

    raw = _elite_dict(elite)
    cell = str(raw.get("cell", "不明"))
    scene_text = "\n".join(_scene_lines(scenes, detailed=True))
    synopsis_block = (
        f"\n## 選定時のあらすじ\n{synopsis.strip()}\n"
        if synopsis and synopsis.strip()
        else ""
    )

    return (
        "あなたは物語の本文作家です。質問や確認を返さず、"
        "以下の情報だけから指定の文字数で物語本文のみを"
        "出力してください。\n\n"
        "# WorldBloom 本文作成依頼\n\n"
        "以下は人間が選定したシミュレーション結果の事実骨格です。"
        "事実の順序と結果を保ちながら、因果、情景、会話、心理を補って"
        "物語本文にしてください。\n\n"
        f"- 世界: {world_meta.get('name', '不明')}\n"
        f"- 登場人物: {_character_text(world_meta)}\n"
        f"- 固定された結末: "
        f"{world_meta.get('target_ending_label', '不明')}\n"
        f"- アーカイブ上の位置: {cell}\n"
        f"{synopsis_block}\n"
        "## 事実骨格と状態推移\n"
        f"{scene_text if scene_text else '- 注目ターンなし'}\n\n"
        "## 執筆指示\n"
        "- 2,000〜4,000字の日本語の物語を書く。\n"
        "- 注目ターンの前後関係、選択の動機、結果への因果をつなぐ。\n"
        "- 伏線の設置と回収は、同じ説明を持つ項目の対応を守る。\n"
        "- 露見では、隠していた側と知った側の心理変化を描く。\n"
        "- 裏切りでは、先行する関係や誓いを踏まえて心理を描く。\n"
        "- 状態値を数値のまま本文に書かず、行動や情景へ翻訳する。\n"
        "- ログにない主要事件、勝敗、所持者交代、結末を追加しない。\n\n"
        "## 出力形式\n"
        "物語本文のみを出力する。タイトル、見出し、制作上の説明、"
        "前置き、質問、確認は出力しない。\n"
    )


def load_settings(path: str | Path) -> tuple[dict[str, Any], str | None]:
    settings_path = Path(path)
    if not settings_path.is_file():
        return {}, f"settings file not found: {settings_path}"
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {}, f"could not read settings: {error}"
    if not isinstance(raw, dict):
        return {}, "settings root must be a JSON object"
    return raw, None


def _backend_config(
    settings: Mapping[str, Any],
    backend: str,
) -> Mapping[str, Any]:
    section: Mapping[str, Any] = settings
    for key in ("output", "perform", "llm"):
        candidate = settings.get(key)
        if isinstance(candidate, Mapping):
            section = candidate
            break
    aliases = (backend, backend.replace("-", "_"))
    for alias in aliases:
        value = section.get(alias)
        if isinstance(value, Mapping):
            return value
    return {}


def _windows_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    kwargs["env"] = environment
    return kwargs


def _run_cli(
    command: Sequence[str],
    prompt: str,
    *,
    timeout: int,
) -> str:
    project_root = Path(__file__).resolve().parents[1]
    temporary_path = Path(
        tempfile.mkdtemp(prefix="worldbloom-llm-")
    ).resolve()

    if (
        temporary_path == project_root
        or project_root in temporary_path.parents
    ):
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise GenerationError(
            "temporary CLI directory is inside the project"
        )

    try:
        result = subprocess.run(
            list(command),
            input=prompt,
            cwd=temporary_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **_windows_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GenerationError(str(error)) from error
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise GenerationError(
            detail[-1000:] or f"CLI exited with {result.returncode}"
        )
    return result.stdout


def _post_json(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    *,
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={
            **dict(headers),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode(
            "utf-8",
            errors="replace",
        )[:1000]
        raise GenerationError(
            f"HTTP {error.code}: {detail}"
        ) from error
    except (OSError, ValueError) as error:
        raise GenerationError(str(error)) from error
    if not isinstance(raw, dict):
        raise GenerationError("API response root is not an object")
    return raw


def generate_text(
    backend: str,
    prompt: str,
    *,
    settings_path: str | Path,
    timeout: int = 600,
) -> GenerationResult:
    """Generate text, or return prompt_only for none/missing API keys."""

    if backend not in BACKENDS:
        raise ValueError(f"unknown backend: {backend}")
    if backend == "none":
        return GenerationResult(status="prompt_only")

    settings, settings_warning = load_settings(settings_path)
    config = _backend_config(settings, backend)

    if backend in {"anthropic", "openai"}:
        api_key = str(config.get("api_key", "")).strip()
        if not api_key:
            warning = (
                f"{backend} API key is missing; "
                "falling back to backend none"
            )
            if settings_warning:
                warning += f" ({settings_warning})"
            return GenerationResult(
                status="prompt_only",
                warning=warning,
            )

    if backend == "claude-cli":
        executable = str(config.get("command", "claude"))
        command = shutil.which(executable)
        if command is None:
            raise GenerationError("claude CLI was not found")

        arguments = [command, "-p"]
        model = str(config.get("model", "")).strip()
        if model:
            arguments.extend(["--model", model])

        text = _run_cli(
            arguments,
            prompt,
            timeout=timeout,
        )
        return GenerationResult(
            status="ok",
            text=_validate_response(text),
        )

    if backend == "codex-cli":
        executable = str(config.get("command", "codex"))
        command = shutil.which(executable)
        if command is None:
            raise GenerationError("codex CLI was not found")

        arguments = [
            command,
            "exec",
            "--skip-git-repo-check",
            "-s",
            "read-only",
        ]
        model = str(config.get("model", "")).strip()
        if model:
            arguments.extend(["-m", model])
        arguments.append("-")

        text = _run_cli(
            arguments,
            prompt,
            timeout=timeout,
        )
        return GenerationResult(
            status="ok",
            text=_validate_response(text),
        )

    if backend == "anthropic":
        model = str(
            config.get("model", "claude-opus-5")
        )
        response = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": str(config["api_key"]),
                "anthropic-version": "2023-06-01",
            },
            {
                "model": model,
                "max_tokens": int(config.get("max_tokens", 4096)),
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            },
            timeout=timeout,
        )
        parts = [
            str(part.get("text", ""))
            for part in response.get("content", [])
            if isinstance(part, Mapping)
            and part.get("type") == "text"
        ]
        return GenerationResult(
            status="ok",
            text=_validate_response("".join(parts)),
        )

    model = str(config.get("model", "gpt-5.6"))
    response = _post_json(
        "https://api.openai.com/v1/responses",
        {
            "Authorization": f"Bearer {config['api_key']}",
        },
        {
            "model": model,
            "input": prompt,
        },
        timeout=timeout,
    )
    parts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, Mapping):
            continue
        for content in item.get("content", []) or []:
            if (
                isinstance(content, Mapping)
                and content.get("type") == "output_text"
            ):
                parts.append(str(content.get("text", "")))
    return GenerationResult(
        status="ok",
        text=_validate_response("".join(parts)),
    )