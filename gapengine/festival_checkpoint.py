"""Strict, generation-committed checkpoints for the fixed-world Choice GA.

ga_state.json is the commit marker. Generation directories are immutable once
referenced by it; archive/summary/evaluation exports are reconstructible views.
This format deliberately has no legacy/personality checkpoint fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import sys
import uuid

from gapengine.festival_choices import ChoiceGenome
from gapengine.qd import Archive, Descriptor, Elite
from scripts.cultural_festival_probe import ROOT

FORMAT = "worldbloom.choice-ga-checkpoint"
VERSION = 1
ADAPTER = "cultural_festival_choices_v1"
CODEC = "festival_choices_v1"
KEYS = {"format", "version", "adapter", "codec", "python", "config",
        "source_manifest", "input_manifest", "completed_generations", "rng_state",
        "population", "history", "previous_results", "archive", "artifacts", "checksum"}


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def seal(state):
    state = deepcopy(state)
    state.pop("checksum", None)
    state["checksum"] = digest(state)
    return state


def read_json(path):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate checkpoint JSON key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Non-finite checkpoint JSON value: " + value)

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Missing/unsafe/oversized Choice checkpoint artifact")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                          parse_constant=invalid)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Unreadable Choice checkpoint artifact") from error


def atomic_write(path, data):
    """Flush before replace; a failed write cannot truncate the old document."""
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Windows does not support opening a directory for fsync.
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def save(out, state):
    state = seal(state)
    atomic_write(out / "ga_state.json", encode(state))
    return state


@contextmanager
def writer_lock(out):
    """OS lock released on process death; a leftover file is not a stale lock."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lock_path = out.parent / ("." + out.name + ".choice-ga.lock")
    if lock_path.is_symlink():
        raise ValueError("Unsafe Choice writer lock")
    with lock_path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Choice output already has an active writer") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def files_manifest(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Unsafe/missing Choice artifact directory")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlinks forbidden in Choice artifacts")
        if path.is_file():
            data = path.read_bytes()
            files[path.relative_to(root).as_posix()] = {
                "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return files


def source_fingerprint():
    paths = [p for name in ("engine", "gapengine") for p in (ROOT / name).rglob("*.py")]
    paths += list((ROOT / "scripts").glob("cultural_festival*.py"))
    if any(p.is_symlink() for p in paths):
        raise ValueError("Symlink source forbidden")
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(paths)}
    return {"files": files, "sha256": digest(files)}


def input_fingerprint(project):
    # Include the entire fixed project and template file sets, not only YAML.
    files = {"project/" + p: value for p, value in files_manifest(project).items()}
    files.update({"templates/" + p: value for p, value in files_manifest(ROOT / "templates").items()})
    return {"files": files, "sha256": digest(files)}


def breed(size, archive, previous, rng):
    from gapengine.evolve import _next_population
    aliases = {f"g{elite.generation}/archive/{'-'.join(cell)}":
               "/".join(elite.exemplar["layers_path"].split("/")[:2])
               for cell, elite in archive.cells.items()}
    return [(genome, [aliases.get(parent, parent) for parent in parents])
            for genome, parents in _next_population(size, archive, previous, rng,
                                                    genome_codec=ChoiceGenome)]


def population_dict(population):
    return [{"genome": genome.to_dict(), "parents": list(parents)} for genome, parents in population]


def insert_results(archive, results):
    archive.freeze_thresholds([r["volatility"] for r in results])
    for result in results:
        exemplar = max(result["runs"], key=lambda r: r["quality"])
        descriptor = Descriptor(result["category"], result["volatility"],
                                archive.bin_for(result["volatility"]))
        result["cell"] = [descriptor.category, descriptor.volatility_bin]
        archive.insert(Elite(ChoiceGenome.from_dict(result["genome"]), result["quality"],
            descriptor, result["reach_rate"],
            {"layers_path": exemplar["layers_path"], "seed": exemplar["seed"]},
            result["generation"], tuple(result["parents"])))


def new_state(config, sources, inputs):
    from gapengine.evolve import _rng_state_to_json
    rng = random.Random(config["ga_seed"])
    population = [(ChoiceGenome.random(rng), []) for _ in range(config["population"])]
    return {"format": FORMAT, "version": VERSION, "adapter": ADAPTER, "codec": CODEC,
            "python": [sys.implementation.name, *sys.version_info[:3]], "config": config,
            "source_manifest": sources, "input_manifest": inputs,
            "completed_generations": 0, "rng_state": _rng_state_to_json(rng),
            "population": population_dict(population), "history": [], "previous_results": [],
            "archive": Archive().to_dict(), "artifacts": {}}


def validate(out, config, sources, inputs):
    """Validate everything before exports, cleanup, breeding or evaluation."""
    from gapengine.evolve import _rng_state_to_json, _rng_state_from_json
    state = read_json(out / "ga_state.json")
    try:
        if (not isinstance(state, dict) or set(state) != KEYS
                or state["format"] != FORMAT or type(state["version"]) is not int
                or state["version"] != VERSION or state["adapter"] != ADAPTER
                or state["codec"] != CODEC):
            raise ValueError("Choice checkpoint family/schema/codec mismatch")
        if state != seal(state):
            raise ValueError("Choice checkpoint checksum mismatch")
        for key, expected in (("config", config), ("source_manifest", sources),
                ("input_manifest", inputs),
                ("python", [sys.implementation.name, *sys.version_info[:3]])):
            if state[key] != expected:
                raise ValueError("Choice checkpoint " + key + " mismatch")
        completed = state["completed_generations"]
        if type(completed) is not int or not 0 <= completed <= 4:
            raise ValueError("Invalid completed Choice generation count")
        if (not isinstance(state["history"], list) or len(state["history"]) != completed
                or set(state["artifacts"]) != {f"g{g}" for g in range(completed)}):
            raise ValueError("Choice generation journal mismatch")
        rng = random.Random(config["ga_seed"])
        population = [(ChoiceGenome.random(rng), []) for _ in range(config["population"])]
        archive = Archive()
        previous = []
        known_ids = set()
        for generation, results in enumerate(state["history"]):
            if generation:
                population = breed(config["population"], archive, previous, rng)
            if not isinstance(results, list) or len(results) != config["population"]:
                raise ValueError("Choice population journal mismatch")
            for index, (result, (genome, parents)) in enumerate(zip(results, population)):
                candidate_id = f"g{generation}/ind-{index}"
                if (type(result["generation"]) is not int or result["generation"] != generation
                        or type(result["index"]) is not int or result["index"] != index
                        or result["candidate_id"] != candidate_id or result["parents"] != parents
                        or any(parent not in known_ids for parent in parents)
                        or ChoiceGenome.from_dict(result["genome"]) != genome
                        or len(result["runs"]) != len(config["seeds"])):
                    raise ValueError("Choice candidate/parent/codec journal mismatch")
                for seed, run in zip(config["seeds"], result["runs"]):
                    expected = f"{candidate_id}/seed-{seed}"
                    if (run["seed"] != seed or run["evaluation_id"] != expected
                            or run["layers_path"] != expected + "/layers.jsonl"):
                        raise ValueError("Choice evaluation journal mismatch")
                if read_json(out / candidate_id / "result.json") != result:
                    raise ValueError("Choice candidate log differs from checkpoint")
            known_ids.update(r["candidate_id"] for r in results)
            if read_json(out / f"g{generation}" / "results.json") != results:
                raise ValueError("Choice generation log differs from checkpoint")
            if files_manifest(out / f"g{generation}") != state["artifacts"][f"g{generation}"]:
                raise ValueError("Choice committed artifact corruption")
            insert_results(archive, deepcopy(results))
            previous = results
        if (state["previous_results"] != previous or state["population"] != population_dict(population)
                or state["rng_state"] != _rng_state_to_json(rng)
                or state["archive"] != archive.to_dict()):
            raise ValueError("Choice RNG/population/Archive journal mismatch")
        # Strict codec round trip instead of Archive's permissive legacy loader.
        if Archive.from_dict(state["archive"], genome_codec=ChoiceGenome).to_dict() != state["archive"]:
            raise ValueError("Choice Archive codec mismatch")
        probe = random.Random()
        probe.setstate(_rng_state_from_json(state["rng_state"]))
        validate_pending(out, state)
    except (KeyError, TypeError, IndexError, OverflowError) as error:
        raise ValueError("Malformed Choice checkpoint") from error
    return state


def marker(state):
    return {"format": FORMAT, "version": VERSION, "generation": state["completed_generations"],
            "previous_checkpoint": state["checksum"]}


def validate_pending(out, state):
    done = state["completed_generations"]
    for path in out.iterdir():
        if path.name.startswith("g") and path.name[1:].isdigit() and int(path.name[1:]) >= done:
            if path.name != f"g{done}" or read_json(path / "generation.json") != marker(state):
                raise ValueError("Unowned/unexpected Choice generation; refusing cleanup")
            files_manifest(path)  # refuse symlinks before any removal
    pending = out / ".choice-pending"
    if pending.exists() or pending.is_symlink():
        pending_marker = read_json(pending / "transaction.json")
        leftover_committed = (done > 0 and pending_marker == read_json(
            out / f"g{done-1}" / "generation.json")
            and set(files_manifest(pending)) == {"transaction.json"})
        if pending_marker != marker(state) and not leftover_committed:
            raise ValueError("Unowned Choice transaction; refusing cleanup")
        files_manifest(pending)


def cleanup(out, state):
    # Called only after validating both the checkpoint and every committed file.
    pending = out / ".choice-pending"
    if pending.exists():
        shutil.rmtree(pending)
    generation = out / f"g{state['completed_generations']}"
    if generation.exists():
        shutil.rmtree(generation)


def start_generation(out, state):
    pending = out / ".choice-pending"
    pending.mkdir()
    atomic_write(pending / "transaction.json", encode(marker(state)))
    generation = pending / f"g{state['completed_generations']}"
    generation.mkdir()
    atomic_write(generation / "generation.json", encode(marker(state)))
    return pending


def publish_generation(stage, out, generation, state):
    directory = stage / f"g{generation}"
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    if os.name != "nt":
        directories = [directory, *(p for p in directory.rglob("*") if p.is_dir())]
        for path in reversed(directories):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    os.replace(stage / f"g{generation}", out / f"g{generation}")
    state = save(out, state)
    shutil.rmtree(stage)
    return state


def exports(out, state):
    fingerprints = [r["decision_fingerprint"] for gen in state["history"]
                    for ind in gen for r in ind["runs"]]
    config = state["config"]
    summary = {"adapter": ADAPTER, "ga_evolution": True, "population": config["population"],
        "generations": state["completed_generations"], "seeds": config["seeds"],
        "individuals": config["population"] * state["completed_generations"],
        "distinct_action_sequences": len(set(fingerprints)), "history": state["history"],
        "source_manifest": state["source_manifest"], "input_manifest": state["input_manifest"],
        "personality_evolved": False, "goals_evolved": False, "world_changed": False,
        "jev_calls": 0, "fitness_absence_bonus": 0, "archive_cells": len(state["archive"]["cells"]),
        "checkpoint_format": FORMAT, "checkpoint_version": VERSION,
        "limitations": ["bounded CPU experiment", "no Choice GUI/lineage UI", "no Jev",
                        "no panel completion", "no joint transport"]}
    evaluations = b"".join(encode({"candidate_id": ind["candidate_id"],
        "generation": ind["generation"], "index": ind["index"], "parents": ind["parents"],
        "genome_sha256": ChoiceGenome.from_dict(ind["genome"]).sha256,
        "quality": ind["quality"], "reach_rate": ind["reach_rate"], "cell": ind["cell"],
        "evaluation_id": run["evaluation_id"], "seed": run["seed"],
        "layers_sha256": run["native_layers_sha256"], "seed_quality": run["quality"]})
        for gen in state["history"] for ind in gen for run in ind["runs"])
    for name, data in (("archive.json", encode(state["archive"])),
                       ("summary.json", encode(summary)), ("evaluations.jsonl", evaluations)):
        path = out / name
        if path.is_symlink():
            raise ValueError("Unsafe Choice export path")
        if not path.is_file() or path.read_bytes() != data:
            atomic_write(path, data)
