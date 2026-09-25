"""Single-source GA parallelism: repo settings.json's "evolution" section.

WB-UI-021 moved generation settings out of run configs into settings.json's
"output" section because they are server-side operational concerns, not part
of what an experiment *means*. The GA process count is the same kind of
concern -- how many CPU cores this PC devotes to evaluation, not a fact about
the story being explored (processes changing never changes archive.json,
confirmed by measurement) -- so it gets the same treatment here.
"""
from __future__ import annotations

from copy import deepcopy
import os

from execution.output_settings import _read_settings
from execution.provenance import ConfigError, atomic_json
from gapengine.gpu_guard import DEFAULT_THERMAL

PAUSE_MIN, PAUSE_MAX = 50, 95
# resume_at follows pause_at with the default's gap (78 -> 70), so a lowered
# pause_at can never sit below resume_at and resume instantly.
RESUME_GAP = DEFAULT_THERMAL["pause_at"] - DEFAULT_THERMAL["resume_at"]


def default_processes():
    return min(8, os.cpu_count() or 1)


def read_evolution_settings(settings_path):
    settings = _read_settings(settings_path)
    section = settings.get("evolution")
    section = section if isinstance(section, dict) else {}
    default = default_processes()
    cpu_count = os.cpu_count() or 1
    processes = section.get("processes")
    if type(processes) is not int or not (1 <= processes <= cpu_count):
        processes = default
    # The GPU thermal guard rides along on the same ⚙ 計算 form. It lives in
    # output.gpu_guard.thermal (gapengine/gpu_guard.py); only the thermal part
    # is toggled here -- gpu_guard itself also owns the GPU lease and
    # llama-server auto-start, which must stay on.
    output = settings.get("output")
    guard = output.get("gpu_guard") if isinstance(output, dict) else None
    thermal = guard.get("thermal") if isinstance(guard, dict) else None
    thermal = thermal if isinstance(thermal, dict) else {}
    pause_at = thermal.get("pause_at")
    if type(pause_at) is not int or not (PAUSE_MIN <= pause_at <= PAUSE_MAX):
        pause_at = DEFAULT_THERMAL["pause_at"]
    return {"processes": processes, "default": default, "cpu_count": cpu_count,
            "thermal_enabled": isinstance(guard, dict) and thermal.get("enabled") is not False,
            "pause_at": pause_at, "pause_default": DEFAULT_THERMAL["pause_at"],
            "pause_min": PAUSE_MIN, "pause_max": PAUSE_MAX}


def write_evolution_settings(settings_path, changes):
    if settings_path is None:
        raise ConfigError("settings", "設定ファイルの場所が未設定です")
    if not isinstance(changes, dict) or set(changes) - {"processes", "thermal_enabled", "pause_at"}:
        raise ConfigError("settings", "未対応の設定項目があります")
    settings = _read_settings(settings_path)
    cpu_count = os.cpu_count() or 1
    if "processes" in changes:
        processes = changes["processes"]
        if type(processes) is not int or not (1 <= processes <= cpu_count):
            raise ConfigError("processes", "1〜{}の整数を指定してください".format(cpu_count))
        section = settings.get("evolution")
        section = dict(section) if isinstance(section, dict) else {}
        section["processes"] = processes
        settings = deepcopy(settings)
        settings["evolution"] = section
    current = read_evolution_settings(settings_path)
    enabled = changes.get("thermal_enabled", current["thermal_enabled"])
    pause_at = changes.get("pause_at", current["pause_at"])
    # Only touch gpu_guard when the thermal values really change: writing it
    # into a settings.json without gpu_guard would also switch on the lease
    # and llama-server auto-start as a side effect of saving processes.
    if (enabled, pause_at) != (current["thermal_enabled"], current["pause_at"]):
        if type(enabled) is not bool:
            raise ConfigError("thermal_enabled", "true か false を指定してください")
        if type(pause_at) is not int or not (PAUSE_MIN <= pause_at <= PAUSE_MAX):
            raise ConfigError("pause_at", "{}〜{}の整数を指定してください".format(PAUSE_MIN, PAUSE_MAX))
        settings = deepcopy(settings)
        output = settings.get("output") if isinstance(settings.get("output"), dict) else {}
        guard = output.get("gpu_guard") if isinstance(output.get("gpu_guard"), dict) else {}
        thermal = guard.get("thermal") if isinstance(guard.get("thermal"), dict) else {}
        thermal.update(enabled=enabled, pause_at=pause_at, resume_at=pause_at - RESUME_GAP)
        guard["thermal"] = thermal
        output["gpu_guard"] = guard
        settings["output"] = output
    atomic_json(settings_path, settings)
    return read_evolution_settings(settings_path)
