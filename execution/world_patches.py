"""Single-lock snapshot boundary for world-expansion consumers."""
from contextlib import contextmanager
from pathlib import Path

import yaml

from execution.provenance import ConfigError, directory_lock
from gapengine.world_patch import PatchError, materialize, read_stack, template_identifiers, verify_stack
from gapengine.world_patch_inputs import inputs_digest, read_subjects, resolve_references


@contextmanager
def patch_lock(project):
    try:
        with directory_lock(Path(project) / "patches"):
            yield
    except ConfigError as error:
        if error.code == "conflict":
            raise PatchError("拡張パッチを更新中です。少し待ってやり直してください") from error
        raise


def project_inputs(project, template, *, repo_root=None):
    project = Path(project)
    world = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))
    people = read_subjects(project / "subjects")
    refs = resolve_references(world, project, repo_root) if repo_root else resolve_references(world, project)
    return world, people, refs, inputs_digest(world, people, template, refs)


def applicable_snapshot(project, template, *, repo_root=None):
    """Caller owns patch_lock. Return one verified stack and base input set."""
    verified = verify_stack(project)
    stack = read_stack(project)
    world, people, refs, base_digest = project_inputs(project, template, repo_root=repo_root)
    if verified and (stack.get("base_inputs_digest") != base_digest
                     or stack.get("template_id") != Path(template).name):
        raise PatchError("世界のベースが承認時から変わっています。reopen して再検査・再承認してください")
    return verified, stack, world, people, refs, base_digest


def expanded_snapshot(project, template, *, repo_root=None):
    with patch_lock(project):
        verified, stack, world, people, refs, base_digest = applicable_snapshot(project, template, repo_root=repo_root)
        world, people = materialize(world, people, [p for p, raw in verified],
                                    reserved=template_identifiers(template), check_budgets=False)
        return world, people, verified
