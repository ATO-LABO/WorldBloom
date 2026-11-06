"""Explicit loader for frozen slot-engine regressions."""

from engine.subject import Subject
from frozen_world import load_frozen_world
from scripts import cultural_festival_probe as probe


def load_frozen_festival(*, legacy=False, project=probe.PROJECT):
    if legacy:
        return probe.load(legacy=True, project=project)
    world = load_frozen_world(project / "world.yaml")
    subjects = [Subject.from_yaml(path) for path in sorted((project / "subjects").glob("*.yaml"))]
    people = {subject.id: subject for subject in subjects}
    world.resolve_truth(0)
    world.bind_subjects(people)
    return world, people
