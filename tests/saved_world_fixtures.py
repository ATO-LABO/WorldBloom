"""Current serialized worlds for tests that exercise saved-world APIs."""
from pathlib import Path
import shutil

import yaml

from engine.world_format import RETIRED_FIELDS, initialize, validate


def current_world_yaml(definition, *, template="momotaro"):
    world = dict(definition)
    world.setdefault("gapengine", {
        "action_graph": f"templates/{template}/action_graph.yaml",
        "effects": f"templates/{template}/effects.yaml",
    })
    return yaml.safe_dump(initialize(world), allow_unicode=True, sort_keys=False)


def install_current_worlds(repo, *project_ids, template=None):
    """Prepare explicitly selected, disposable copies of ordinary worlds."""
    repo = Path(repo)
    if repo.resolve() == Path(__file__).resolve().parents[1]:
        raise ValueError("Saved-world fixtures must be outside the source checkout")
    for ident in project_ids:
        path = repo / "projects" / ident / "world.yaml"
        world = yaml.safe_load(path.read_bytes())
        if "world_format_version" in world:
            validate(world)
            continue
        for field in RETIRED_FIELDS:
            world.pop(field, None)
        world_text = current_world_yaml(world, template=template or ident)
        path.write_bytes(world_text.encode("utf-8"))


def copy_current_world_repo(source, target, *project_ids, runtime=False):
    """Build an isolated repository with only the named world fixtures."""
    source, target = Path(source), Path(target)
    shutil.copytree(source / "templates", target / "templates")
    for ident in project_ids:
        shutil.copytree(source / "projects" / ident, target / "projects" / ident)
    install_current_worlds(target, *project_ids)
    if runtime:
        for package in ("engine", "gapengine", "execution", "scripts"):
            shutil.copytree(source / package, target / package, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(source / "requirements.txt", target / "requirements.txt")
    return target
