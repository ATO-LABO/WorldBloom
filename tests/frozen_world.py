"""Test-only loader preserving frozen definitions; public format guards stay strict."""
from pathlib import Path
from engine.world import World, load_yaml


def load_frozen_world(path, *, action_graph_path=None):
    source = Path(path)
    raw = load_yaml(source)
    if not isinstance(raw, dict):
        raise ValueError(f"World YAML must contain a mapping: {source}")
    return World(raw, source, action_graph_path=action_graph_path)


def evolve_frozen(cfg, *, observer=None):
    """Explicit Python-only regression injection, propagated to spawned workers."""
    from gapengine.evolve import evolve
    return evolve(cfg, observer=observer, world_loader=load_frozen_world)


def run_frozen(function, *args, **kwargs):
    return function(*args, **kwargs, world_loader=load_frozen_world)


def modernize_cli_project(project):
    """Convert only disposable CLI-test inputs; never rewrite checked-in worlds."""
    import yaml
    from engine.world_format import initialize, RETIRED_FIELDS
    project = Path(project).resolve()
    repo = Path(__file__).resolve().parents[1]
    if project.is_relative_to(repo / "projects"):
        raise ValueError("CLI fixture must be an isolated copy")
    path = project / "world.yaml"
    definition = yaml.safe_load(path.read_bytes())
    for key in RETIRED_FIELDS:
        definition.pop(key, None)
    definition.setdefault("gapengine", {"action_graph": str(repo / "templates/momotaro/action_graph.yaml"),
                                        "effects": str(repo / "templates/momotaro/effects.yaml")})
    path.write_text(yaml.safe_dump(initialize(definition), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return project
