"""Historical engine construction must not weaken saved-world admission."""
from pathlib import Path
import unittest
from engine.world import World
from engine.stamina import modern
from frozen_world import load_frozen_world

ROOT = Path(__file__).resolve().parents[1]


class FrozenWorldTests(unittest.TestCase):
    def test_frozen_loader_preserves_source_and_intrinsic_mechanics(self):
        path = ROOT / "projects/momotaro/world.yaml"
        before = path.read_bytes()
        world = load_frozen_world(path)
        self.assertIsNone(world.action_usage)
        self.assertTrue(world.mechanics_enabled("stamina_management"))
        self.assertFalse(modern(world))
        self.assertTrue(world.action_enabled("rest"))
        self.assertFalse(world.action_enabled("start_relationship"))
        self.assertFalse(hasattr(world, "_date_labels"))
        self.assertEqual(path.read_bytes(), before)

    def test_public_loader_still_rejects_the_same_legacy_fixture(self):
        with self.assertRaises(ValueError):
            World.from_yaml(ROOT / "projects/momotaro/world.yaml")

    def test_current_policy_retains_modern_stamina(self):
        world = World.from_yaml(ROOT / "projects/momotaro_plus3_new/world.yaml")
        self.assertIsNotNone(world.action_usage)
        self.assertEqual(modern(world), world.mechanics_enabled("stamina_management"))
