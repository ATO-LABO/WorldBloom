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

from execution.provenance import ConfigError, atomic_json, read_json


def default_processes():
    return min(8, os.cpu_count() or 1)


def _read_settings(settings_path):
    if settings_path is None:
        return {}
    try:
        settings = read_json(settings_path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ConfigError("settings", "settings.json を読めません") from error
    if not isinstance(settings, dict):
        raise ConfigError("settings", "settings.json を読めません")
    return settings


def read_evolution_settings(settings_path):
    settings = _read_settings(settings_path)
    section = settings.get("evolution")
    section = section if isinstance(section, dict) else {}
    default = default_processes()
    cpu_count = os.cpu_count() or 1
    processes = section.get("processes")
    if type(processes) is not int or not (1 <= processes <= cpu_count):
        processes = default
    return {"processes": processes, "default": default, "cpu_count": cpu_count}


def write_evolution_settings(settings_path, changes):
    if settings_path is None:
        raise ConfigError("settings", "設定ファイルの場所が未設定です")
    if not isinstance(changes, dict) or set(changes) - {"processes"}:
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
    atomic_json(settings_path, settings)
    return read_evolution_settings(settings_path)
