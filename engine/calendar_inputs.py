"""Project-local inputs and explicit schema3 calendar execution snapshots.

Input identities and file sets are declared, never inferred from a scenario
directory. The snapshot manifest is an atomic commit marker in an exclusively
created destination. Existing inputs, snapshots and directories are protected.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, is_dataclass
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
from uuid import uuid4

import yaml


MODE = "calendar_promises_v2"
MANIFEST = "execution-version.json"
SCHEMA_VERSION = 3
FILE_LIMIT = 8 * 1024 * 1024
VERSION_KEYS = {"schema_version", "contract", "world", "configuration", "calendar", "inputs",
                "source_inputs", "source_world", "metadata", "engine_supported", "execution_ready", "id"}


def _validation_boundary(function):
    @wraps(function)
    def validate(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (TypeError, KeyError, AttributeError, OverflowError, UnicodeError, RecursionError) as error:
            raise ValueError("Malformed calendar project input to " + function.__name__) from error
    return validate


def _finite_json(value, *, depth=0, stack=None):
    if depth > 64:
        raise ValueError("Input nesting exceeds the supported bound")
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("Invalid Unicode JSON string") from error
        return
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Non-finite input value")
        return
    if type(value) not in (dict, list):
        raise ValueError("Inputs and provenance must contain JSON values")
    stack = set() if stack is None else stack
    if id(value) in stack:
        raise ValueError("Cyclic input")
    stack.add(id(value))
    values = value.values() if isinstance(value, dict) else value
    if isinstance(value, dict) and any(type(key) is not str for key in value):
        raise ValueError("Input mapping keys must be strings")
    if isinstance(value, dict):
        for key in value:
            _finite_json(key, depth=depth + 1, stack=stack)
    for item in values:
        _finite_json(item, depth=depth + 1, stack=stack)
    stack.remove(id(value))


def _canonical(value):
    _finite_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _identifier(value, label):
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 256:
        raise ValueError("Invalid " + label)
    return value


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value) or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive or any(p in ("", ".", "..") for p in value.split("/"))):
        raise ValueError("An explicit project-relative path without parent/drive aliases is required")
    return value


def _no_links(path):
    absolute = Path(path).absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Symlink/junction input or destination component is forbidden")
    return absolute.resolve()


def _world_path(path):
    result = _no_links(path)
    if not result.is_file() or result.stat().st_size > FILE_LIMIT:
        raise ValueError("World input is missing or oversized")
    if result.name == MANIFEST:
        raise ValueError("World filename collides with snapshot manifest")
    return result


@_validation_boundary
def safe_project_path(world_path, relative):
    """Validate every component before resolve, including internal aliases."""
    world = _world_path(world_path)
    relative = _relative(relative)
    result = _no_links(world.parent / relative)
    if world.parent not in result.parents or not result.is_file() or result.stat().st_size > FILE_LIMIT:
        raise ValueError("Project input is missing, oversized or outside its project")
    return result


@_validation_boundary
def validate_execution_config(config):
    if (not isinstance(config, dict) or not {"mode", "contract", "subjects"} <= config.keys()
            or set(config) - {"mode", "contract", "subjects", "fixed_version"}
            or config["mode"] != MODE or ("fixed_version" in config and config["fixed_version"] is not True)):
        raise ValueError("Invalid explicit calendar execution configuration")
    _relative(config["contract"])
    roster = config["subjects"]
    if not isinstance(roster, list) or not 1 <= len(roster) <= 1000:
        raise ValueError("Bounded nonempty explicit subject roster required")
    ids, paths = set(), {config["contract"].casefold(), MANIFEST.casefold()}
    if config["contract"].casefold() == MANIFEST.casefold():
        raise ValueError("Contract path collides with manifest")
    for record in roster:
        if not isinstance(record, dict) or set(record) != {"id", "path"}:
            raise ValueError("Subject roster entries need exactly id and path")
        ident, path = _identifier(record["id"], "subject id"), _relative(record["path"])
        if ident in ids or path.casefold() in paths:
            raise ValueError("Duplicate subject id or input file collision")
        ids.add(ident); paths.add(path.casefold())
    return deepcopy(config)


class _UniqueLoader(yaml.SafeLoader):
    pass


def _yaml_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in mapping:
            raise ValueError("Non-string or duplicate YAML mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _yaml_text(text):
    try:
        value = yaml.load(text, Loader=_UniqueLoader)
    except (UnicodeError, yaml.YAMLError) as error:
        raise ValueError("Unreadable YAML project input") from error
    _finite_json(value)
    if not isinstance(value, dict):
        raise ValueError("Project YAML input must be a mapping")
    return value


def _yaml(path):
    try:
        return _yaml_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ValueError("Unreadable YAML project input") from error


@_validation_boundary
def read_project_yaml(world_path, relative):
    """Parse the verified project's current bytes, bypassing legacy mtime cache."""
    return _yaml(safe_project_path(world_path, relative))


def _input_files(world_path, config=None):
    world = _world_path(world_path)
    raw = _yaml(world)
    config = validate_execution_config(raw.get("slot_execution") if config is None else config)
    if raw.get("slot_execution") != config:
        raise ValueError("Execution configuration differs from the world declaration")
    references = [world.name, config["contract"], *[s["path"] for s in config["subjects"]]]
    gap = raw.get("gapengine") or {}
    if not isinstance(gap, dict):
        raise ValueError("Invalid world gapengine definition")
    graph = gap.get("action_graph")
    if graph is not None:
        references.append(_relative(graph))
    names, inodes, files = set(), set(), {}
    for relative in references:
        relative = _relative(relative)
        path = world if relative == world.name else safe_project_path(world, relative)
        stat = path.stat(); inode = (stat.st_dev, stat.st_ino)
        if relative.casefold() in names or inode in inodes or relative.casefold() == MANIFEST.casefold():
            raise ValueError("Input file role/path/inode collision")
        names.add(relative.casefold()); inodes.add(inode); files[relative] = path
    return world, raw, config, files


def _file_manifest(files):
    return {name: {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, path in sorted(files.items()) for data in [path.read_bytes()]}


def _subject_data(world, config, files):
    contract = _yaml(files[config["contract"]])
    ids = {item["id"] for item in config["subjects"]}
    if not isinstance(contract.get("roles"), dict) or set(contract["roles"]) != ids:
        raise ValueError("Contract roles must exactly match declared subjects")
    for field in ("protagonist", "antagonist"):
        if world.get(field) not in ids:
            raise ValueError("Unknown world " + field)
    zones = world.get("zones")
    if not isinstance(zones, list) or not zones or any(not isinstance(z, dict) or "name" not in z for z in zones):
        raise ValueError("World places must be explicitly declared")
    names = [_identifier(z["name"], "place") for z in zones]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate world place")
    places = set(names)
    raw_items, raw_facts = world.get("items", []), world.get("facts", [])
    if (not isinstance(raw_items, list) or not isinstance(raw_facts, list)
            or any(not isinstance(i, dict) or not isinstance(i.get("name"), str) for i in raw_items)
            or any(not isinstance(f, dict) or not isinstance(f.get("id"), str) for f in raw_facts)):
        raise ValueError("Invalid world item/fact declarations")
    items = {i["name"] for i in raw_items}
    facts = {f["id"]: f for f in raw_facts}
    if ids & (places | items | set(facts)):
        raise ValueError("Subject ids collide with place/item/fact identities")
    output = {}
    for item in config["subjects"]:
        value = _yaml(files[item["path"]])
        if value.get("id") != item["id"]:
            raise ValueError("Declared subject id differs from YAML id")
        ranges = value.get("range")
        if (not isinstance(ranges, dict) or not isinstance(ranges.get("zones"), list)
                or not ranges["zones"] or any(not isinstance(z, str) for z in ranges["zones"])
                or len(ranges["zones"]) != len(set(ranges["zones"]))
                or not set(ranges["zones"]) <= places or ranges.get("entry") not in ranges["zones"]):
            raise ValueError("Subject entry/range contains undeclared places")
        if "exclude" in ranges:
            if not isinstance(ranges["exclude"], list):
                raise ValueError("Excluded ranges must be a list")
            for exclusion in ranges["exclude"]:
                if (not isinstance(exclusion, dict) or set(exclusion) - {"zones", "until_item"}
                        or not isinstance(exclusion.get("zones", []), list)
                        or any(not isinstance(z, str) or z not in places for z in exclusion.get("zones", []))
                        or (exclusion.get("until_item") is not None and exclusion["until_item"] not in items)):
                    raise ValueError("Invalid excluded zones/item")
        for field in ("relations", "beliefs_about"):
            mapping = value.get(field, {})
            if not isinstance(mapping, dict) or not set(mapping) <= ids:
                raise ValueError("Unknown subject reference in " + field)
        companions = value.get("companions")
        if companions is not None and (not isinstance(companions, list) or any(c not in ids for c in companions)):
            raise ValueError("Unknown companion")
        goal = value.get("goal", {}) or {}
        if not isinstance(goal, dict):
            raise ValueError("Invalid subject goal")
        if goal.get("target") is not None and goal["target"] not in items:
            raise ValueError("Unknown goal item")
        if goal.get("deliver_to") is not None and goal["deliver_to"] not in places:
            raise ValueError("Unknown goal delivery place")
        if not isinstance(goal.get("obstacles", []), list) or any(o not in ids for o in goal.get("obstacles", [])):
            raise ValueError("Unknown goal obstacle actor")
        inventory = value.get("inventory", {}) or {}
        if not isinstance(inventory, dict) or any(type(q) is not int or q < 0 for q in inventory.values()):
            raise ValueError("Inventory quantities must be nonnegative integers")
        if not set(inventory) <= items:
            raise ValueError("Unknown inventory item")
        knowledge = value.get("knowledge", [])
        if not isinstance(knowledge, list) or any(f not in facts or facts[f].get("values") is not None for f in knowledge):
            raise ValueError("Unknown or valued fact used as boolean knowledge")
        beliefs = value.get("beliefs", {}) or {}
        if not isinstance(beliefs, dict):
            raise ValueError("Beliefs must be a mapping")
        for fact, belief in beliefs.items():
            if (fact not in facts or not isinstance(belief, dict)
                    or "value" not in belief
                    or str(belief["value"]) not in {str(v) for v in (facts[fact].get("values") or [])}):
                raise ValueError("Unknown valued belief")
        for modifier in value.get("modifiers", []) or []:
            if not isinstance(modifier, dict):
                raise ValueError("Invalid subject modifier")
            targets = modifier.get("affinity_cap_targets", []) or []
            if isinstance(targets, str):
                targets = [targets]
            if not isinstance(targets, list) or any(t not in ids for t in targets):
                raise ValueError("Unknown modifier affinity target")
        traits = value.get("traits")
        if not isinstance(traits, dict) or any(type(t) not in (int, float) or not math.isfinite(t) for t in traits.values()):
            raise ValueError("Subject traits must be finite numbers")
        output[item["id"]] = value
    return output


@_validation_boundary
def read_subjects(world_path, config):
    from engine.subject import Subject
    world, raw, config, files = _input_files(world_path, config)
    before = _file_manifest(files)
    if _yaml(world) != raw:
        raise ValueError("World input changed during subject loading")
    definitions = _subject_data(raw, config, files)
    people = {record["id"]: Subject.from_definition(definitions[record['id']], source=files[record['path']])
              for record in config["subjects"]}
    if before != _file_manifest(files):
        raise ValueError("Project input changed during subject loading")
    return people


def _plain(value):
    if is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=lambda item: _canonical(item))
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


@_validation_boundary
def subject_spec(subject):
    """Authored/initial Subject values; policy and learned counters are runtime."""
    omitted = {"policy", "decision_history", "gather_progress", "gathered"}
    value = {f.name: _plain(getattr(subject, f.name)) for f in fields(subject) if f.name not in omitted}
    return json.loads(_canonical(value))


@_validation_boundary
def load_project(world_path):
    # Validate the dependency boundary before World can apply legacy file lookup.
    path, raw, config, files = _input_files(world_path)
    before = _file_manifest(files)
    if _yaml(path) != raw:
        raise ValueError("World input changed during loading")
    _subject_data(raw, config, files)
    from engine.world import World
    world = World(raw, path)
    people = read_subjects(path, config)
    if before != _file_manifest(files):
        raise ValueError("Project input changed during loading")
    world.calendar_subject_sources = {record["id"]: before[record["path"]]["sha256"] for record in config["subjects"]}
    world.calendar_subject_specs = {ident: subject_spec(subject) for ident, subject in people.items()}
    world.calendar_input_provenance = {"world": path.name, "configuration": deepcopy(config), "inputs": before}
    from engine.calendar_v2 import compile_calendar
    calendar = compile_calendar(world.slot_contract["calendar"], timezone=world.slot_contract["timezone"],
                                scheduled_events=list(world.scheduled_events))
    version = validate_fixed_version(path, config, calendar)
    world.calendar_input_provenance["fixed_version_id"] = None if version is None else version["id"]
    return world, people


def _json_file(path):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate fixed-version JSON key")
            result[key] = value
        return result
    if path.is_symlink() or not path.is_file() or path.stat().st_size > FILE_LIMIT:
        raise ValueError("Fixed version manifest is missing, unsafe or oversized")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda s: (_ for _ in ()).throw(ValueError("Non-finite " + s)))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Unreadable fixed version") from error
    _finite_json(value)
    return value


def _manifest_shape(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("Nonempty explicit input manifest required")
    for name, record in value.items():
        _relative(name)
        if (not isinstance(record, dict) or set(record) != {"size_bytes", "sha256"}
                or type(record["size_bytes"]) is not int or not 0 <= record["size_bytes"] <= FILE_LIMIT
                or not isinstance(record["sha256"], str) or len(record["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in record["sha256"])):
            raise ValueError("Invalid input size/hash record")


def _exact_tree(root, names):
    expected_files = set(names) | {MANIFEST}
    expected_dirs = {p.as_posix() for name in expected_files for p in PurePosixPath(name).parents if p.as_posix() != "."}
    actual_files, actual_dirs = set(), set()
    for path in root.rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Linked fixed-version entry")
        name = path.relative_to(root).as_posix()
        if path.is_file():
            actual_files.add(name)
        elif path.is_dir():
            actual_dirs.add(name)
        else:
            raise ValueError("Unsupported fixed-version entry")
    if actual_files != expected_files or actual_dirs != expected_dirs:
        raise ValueError("Fixed version file/directory set differs from declared dependencies")


@_validation_boundary
def validate_fixed_version(world_path, config, calendar):
    world, raw, config, files = _input_files(world_path, config)
    path = world.parent / MANIFEST
    if not path.exists() and not path.is_symlink():
        if config.get("fixed_version") is True:
            raise ValueError("Required fixed version is missing")
        return None
    version = _json_file(path)
    if (not isinstance(version, dict) or set(version) != VERSION_KEYS
            or type(version["schema_version"]) is not int or version["schema_version"] != SCHEMA_VERSION
            or version["contract"] != MODE or version["world"] != world.name
            or version["engine_supported"] is not True or version["execution_ready"] is not True):
        raise ValueError("Unsupported fixed version schema; schema2 is not migrated implicitly")
    if config.get("fixed_version") is not True or validate_execution_config(version["configuration"]) != config:
        raise ValueError("Fixed version configuration differs")
    if version["calendar"] != calendar:
        raise ValueError("Fixed calendar differs from compiled project calendar")
    if version["id"] != _digest({key: value for key, value in version.items() if key != "id"}):
        raise ValueError("Fixed version checksum mismatch")
    _manifest_shape(version["inputs"]); _manifest_shape(version["source_inputs"])
    if set(version["inputs"]) != set(files) or set(version["source_inputs"]) != set(files):
        raise ValueError("Fixed version input declaration differs")
    if version["inputs"] != _file_manifest(files):
        raise ValueError("Fixed input bytes changed")
    if any(version["source_inputs"][name] != version["inputs"][name] for name in files if name != world.name):
        raise ValueError("Unchanged dependency provenance differs from source input")
    if not isinstance(version["source_world"], str):
        raise ValueError("Source world UTF8 text required")
    original = version["source_world"].encode("utf-8")
    if version["source_inputs"][world.name] != {"size_bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}:
        raise ValueError("Source world bytes differ from recorded provenance")
    original_definition = _yaml_text(version["source_world"])
    original_config = validate_execution_config(original_definition.get("slot_execution"))
    original_definition["slot_execution"] = {**original_config, "fixed_version": True}
    if original_definition != raw:
        raise ValueError("Snapshot changed source world beyond explicit fixed_version flag")
    _exact_tree(world.parent, files)
    return deepcopy(version)


def _write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


@_validation_boundary
def freeze_project(world_path, destination, metadata=None):
    _finite_json(metadata)
    if len(_canonical(metadata)) > 256 * 1024:
        raise ValueError("Provenance metadata is too large")
    source, raw, config, files = _input_files(world_path)
    source_manifest = _file_manifest(files)
    # Decode bytes directly: universal-newline translation would falsify the
    # provenance of otherwise legal CRLF-authored input on Windows or Linux.
    source_world = source.read_bytes().decode("utf-8")
    if {"size_bytes": len(source_world.encode("utf-8")), "sha256": hashlib.sha256(source_world.encode("utf-8")).hexdigest()} != source_manifest[source.name]:
        raise ValueError("Source world bytes changed before freezing")
    world, _people = load_project(source)
    from engine.calendar_v2 import compile_calendar
    compiled = compile_calendar(world.slot_contract["calendar"], timezone=world.slot_contract["timezone"],
                                scheduled_events=list(world.scheduled_events))
    target = _no_links(destination)
    if target.exists() or target.is_symlink():
        raise ValueError("Existing destination is protected, including empty directories")
    # Exclusive claim cannot replace another creator's empty directory. The
    # fixed_version flag prevents loading the partial tree before its commit.
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=False)
    try:
        fixed_config = {**config, "fixed_version": True}
        fixed_world = deepcopy(raw); fixed_world["slot_execution"] = fixed_config
        for name, path in sorted(files.items()):
            data = (yaml.safe_dump(fixed_world, allow_unicode=True, sort_keys=False).encode("utf-8")
                    if name == source.name else path.read_bytes())
            _write_new(target / name, data)
        if source_manifest != _file_manifest(files):
            raise ValueError("Source input changed while freezing")
        _world, _raw, _config, frozen_files = _input_files(target / source.name)
        version = {"schema_version": SCHEMA_VERSION, "contract": MODE, "world": source.name,
                   "configuration": fixed_config, "calendar": compiled, "inputs": _file_manifest(frozen_files),
                   "source_inputs": source_manifest, "source_world": source_world, "metadata": deepcopy(metadata),
                   "engine_supported": True, "execution_ready": True}
        version["id"] = _digest(version)
        encoded = _canonical(version)
        if len(encoded) > FILE_LIMIT:
            raise ValueError("Fixed manifest exceeds its size bound")
        temporary = target / ("." + MANIFEST + "-" + uuid4().hex)
        _write_new(temporary, encoded)
        os.replace(temporary, target / MANIFEST)
        if os.name != "nt":
            descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        validate_fixed_version(target / source.name, fixed_config, compiled)
        return deepcopy(version)
    except BaseException:
        # Only the directory exclusively created by this call is removed.
        shutil.rmtree(target)
        raise
