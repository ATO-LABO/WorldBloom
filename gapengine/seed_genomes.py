"""WB-WORLDGROW-001 段階5b "遺伝子の引き継ぎ": read/write the frozen
inputs/seed_genomes.json that seeds a GA run's generation 0 from a prior
experiment's final archive. Stdlib + gapengine.genome only -- no engine/
execution imports, same discipline as gapengine/world_patch_usage.py (this
must be safe to call from the CLI, the config store, and gapengine.evolve
alike).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from gapengine.genome import Genome


def from_archive(archive: Mapping[str, Any]) -> list[dict[str, Any]]:
    """{cell, quality, generation, genome} per occupied cell of `archive`
    (a loaded archive.json dict), ordered by (-quality, cell) -- the same
    order seed_genomes.json's own "genomes" list is written in."""
    cells = archive.get("cells") if isinstance(archive, Mapping) else None
    if not isinstance(cells, Mapping):
        raise ValueError("archive.cells must be a mapping")
    entries = []
    for cell, elite in cells.items():
        if not isinstance(elite, Mapping):
            raise ValueError(f"archive cell {cell!r} must be a mapping")
        genome = elite.get("genome")
        if not isinstance(genome, Mapping):
            raise ValueError(f"archive cell {cell!r} has no genome mapping")
        try:
            quality = float(elite.get("quality", 0.0))
            generation = int(elite.get("generation", 0))
        except (TypeError, ValueError) as error:
            # A corrupt/hand-edited archive can carry quality/generation as
            # null or a non-numeric value -- float(None)/int(None) raise
            # TypeError, not ValueError. Normalize both to ValueError so
            # every caller (CLI, execution.configs) only ever needs to
            # catch one exception type for "this archive is malformed".
            raise ValueError(f"archive cell {cell!r} has an invalid quality/generation") from error
        entries.append({
            "cell": str(cell),
            "quality": quality,
            "generation": generation,
            "genome": genome,
        })
    entries.sort(key=lambda entry: (-entry["quality"], entry["cell"]))
    return entries


def load(path) -> dict[str, Any]:
    """Read and validate a seed_genomes.json file, returning the full
    document. Raises ValueError on any structural problem (OSError/
    json.JSONDecodeError propagate as-is)."""
    doc = json.loads(Path(path).read_bytes())
    if not isinstance(doc, Mapping) or doc.get("schema_version") != 1:
        raise ValueError("seed_genomes.json: unsupported schema_version")
    genomes = doc.get("genomes")
    if not isinstance(genomes, list) or not genomes:
        raise ValueError("seed_genomes.json: genomes must be a non-empty list")
    for entry in genomes:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("genome"), Mapping):
            raise ValueError("seed_genomes.json: each entry needs a genome mapping")
    return doc


def reconcile(raw_genome: Mapping[str, Any], *, rule_ids: Iterable[str] = ()) -> Genome:
    """A seeded genome, its rule_bits reconciled against this run's own
    rule_ids (a template/meta_evolution change since the source archive was
    captured may have added or dropped rules -- missing bits default to
    enabled, extra bits are dropped, same convention as Genome.crossover)."""
    try:
        genome = Genome.from_dict(raw_genome)
    except (KeyError, TypeError) as error:
        # Genome.from_dict() itself only ever raises ValueError for a
        # missing category_weight entry -- a missing scalar (risk_tolerance/
        # stance_shift_bias/novelty_drive) instead KeyErrors on raw[...],
        # and a non-numeric scalar TypeErrors on float(...). Normalize both
        # to ValueError, same convention as from_archive() above.
        raise ValueError("seed genome is malformed") from error
    ids = sorted(set(map(str, rule_ids)))
    return Genome(
        category_weight=genome.category_weight,
        risk_tolerance=genome.risk_tolerance,
        stance_shift_bias=genome.stance_shift_bias,
        novelty_drive=genome.novelty_drive,
        rule_bits={rule_id: genome.rule_bits.get(rule_id, True) for rule_id in ids},
    )
