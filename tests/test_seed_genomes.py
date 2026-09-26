"""WB-WORLDGROW-001 段階5b: gapengine.seed_genomes's from_archive/load/reconcile."""
import json
import tempfile
import unittest
from pathlib import Path

from gapengine.genome import Genome
from gapengine.seed_genomes import from_archive, load, reconcile


def _genome(**overrides):
    base = Genome.neutral().to_dict()
    base.update(overrides)
    return base


class FromArchiveTests(unittest.TestCase):
    def test_orders_by_quality_desc_then_cell(self):
        archive = {"cells": {
            "II|mid": {"quality": 0.5, "generation": 3, "genome": _genome()},
            "I|low": {"quality": 0.9, "generation": 1, "genome": _genome()},
            "III|high": {"quality": 0.9, "generation": 2, "genome": _genome()},
        }}
        entries = from_archive(archive)
        # Tie on quality (0.9) between "I|low" and "III|high" breaks by cell
        # string order ("|" sorts after "I", so "III|high" < "I|low").
        self.assertEqual([e["cell"] for e in entries], ["III|high", "I|low", "II|mid"])
        self.assertEqual(entries[0]["quality"], 0.9)
        self.assertEqual(entries[0]["generation"], 2)

    def test_rejects_non_mapping_cells(self):
        with self.assertRaises(ValueError):
            from_archive({"cells": "nope"})

    def test_rejects_cell_without_genome(self):
        with self.assertRaises(ValueError):
            from_archive({"cells": {"I|low": {"quality": 0.1}}})


class LoadTests(unittest.TestCase):
    def _write(self, doc):
        temp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        json.dump(doc, temp)
        temp.close()
        return Path(temp.name)

    def test_loads_valid_document(self):
        doc = {"schema_version": 1, "source": {}, "genomes": [{"cell": "I|low", "genome": _genome()}]}
        path = self._write(doc)
        try:
            loaded = load(path)
        finally:
            path.unlink()
        self.assertEqual(loaded["genomes"], doc["genomes"])

    def test_rejects_wrong_schema_version(self):
        path = self._write({"schema_version": 2, "genomes": [{"genome": _genome()}]})
        try:
            with self.assertRaises(ValueError):
                load(path)
        finally:
            path.unlink()

    def test_rejects_empty_genomes(self):
        path = self._write({"schema_version": 1, "genomes": []})
        try:
            with self.assertRaises(ValueError):
                load(path)
        finally:
            path.unlink()

    def test_rejects_entry_without_genome_mapping(self):
        path = self._write({"schema_version": 1, "genomes": [{"cell": "I|low"}]})
        try:
            with self.assertRaises(ValueError):
                load(path)
        finally:
            path.unlink()


class ReconcileTests(unittest.TestCase):
    def test_no_rule_ids_yields_empty_rule_bits(self):
        raw = _genome(rule_bits={"r1": False})
        genome = reconcile(raw, rule_ids=())
        self.assertEqual(genome.rule_bits, {})

    def test_missing_bit_defaults_enabled(self):
        raw = _genome(rule_bits={"r1": False})
        genome = reconcile(raw, rule_ids=("r1", "r2"))
        self.assertEqual(genome.rule_bits, {"r1": False, "r2": True})

    def test_extra_bit_dropped(self):
        raw = _genome(rule_bits={"r1": False, "r2": False})
        genome = reconcile(raw, rule_ids=("r1",))
        self.assertEqual(genome.rule_bits, {"r1": False})

    def test_preserves_scalars(self):
        raw = _genome(risk_tolerance=0.75, stance_shift_bias=-0.5, novelty_drive=0.25)
        genome = reconcile(raw, rule_ids=())
        self.assertEqual(genome.risk_tolerance, 0.75)
        self.assertEqual(genome.stance_shift_bias, -0.5)
        self.assertEqual(genome.novelty_drive, 0.25)


if __name__ == "__main__":
    unittest.main()
