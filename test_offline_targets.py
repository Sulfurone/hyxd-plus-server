"""Keep the finite enemy budget correct across loading, cleanup and restarts."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_offline_ai import Vector


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.pending, self.entities = [], {}
        self.owner = SimpleNamespace(id=1, inWorld=True, spaceID=1, position=Vector(0, 0, 0))
        self.engine = SimpleNamespace(player=lambda: self.owner, entities=self.entities,
            callback=lambda delay, fn: self.pending.append(fn),
            dcursor=lambda: SimpleNamespace(yaw=0), createEntity=Mock(side_effect=self.create),
            destroyEntity=Mock(), collide=self.collide)
        self.state = {'local_enemy_count': 2, 'local_round': {
            'id': 1, 'phase': 'active', 'spawned': 0, 'total': 1}}
        self.log = Mock()
        self.module_patch = patch.dict(sys.modules, {
            'BigWorld': self.engine, 'Math': SimpleNamespace(Vector3=Vector),
            'PhysX': Mock(), 'game': SimpleNamespace(getOfflineAvatarInfo=lambda: {})})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        spec = importlib.util.spec_from_file_location('_targets_test',
            Path(__file__).with_name('offline_targets.py'))
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)

    def collide(self, space, start, end):
        return (Vector(start[0], 0, start[2]),) if start[1] == 2 else None

    def create(self, name, space, vehicle, position, rotation, props):
        entity_id = len(self.entities) + 10
        self.entities[entity_id] = SimpleNamespace(position=position, hp=10000,
            model=SimpleNamespace(sources=('original.model',)))
        return entity_id

    def install(self):
        self.bridge.install(self.owner, self.state, self.log)

    def test_successful_creation_spends_one_finite_slot(self):
        self.install()
        self.pending.pop(0)()
        self.assertEqual(self.state['local_round']['spawned'], 1)
        self.assertEqual(self.state['local_targets'][10]['round_id'], 1)
        self.install()
        self.engine.createEntity.assert_called_once()

    def test_finished_round_does_not_repopulate(self):
        self.state['local_round']['phase'] = 'finished'
        self.install()
        self.assertEqual(self.pending, [])

    def test_restart_cancels_old_pending_spawn(self):
        self.install()
        self.state['local_round'] = dict(self.state['local_round'], id=2)
        self.pending.pop(0)()
        self.engine.createEntity.assert_not_called()

    def test_round_preserves_distant_living_enemy_and_budget(self):
        self.state['local_round']['total'] = 2
        self.entities[10] = SimpleNamespace(position=Vector(500, 0, 0), hp=3000)
        self.state['local_targets'] = {10: {'native_health_initialized': True}}
        self.install()
        self.engine.destroyEntity.assert_not_called()
        self.assertEqual(self.entities[10].hp, 3000)

    def test_missing_body_releases_budget_for_replacement(self):
        self.install()
        self.pending.pop(0)()
        del self.entities[10]
        self.pending.pop(0)()
        self.assertEqual(self.state['local_round']['spawned'], 0)
        self.assertEqual(self.state['local_targets'], {})

    def test_finished_round_cancels_queued_creation(self):
        self.install()
        self.state['local_round']['phase'] = 'finished'
        self.pending.pop(0)()
        self.engine.createEntity.assert_not_called()

    def test_missing_ready_enemy_releases_exhausted_budget(self):
        self.state['local_round']['spawned'] = 1
        self.state['local_targets'] = {10: {'ready': True}}
        self.install()
        self.assertEqual(self.state['local_round']['spawned'], 0)
        self.assertEqual(self.state['local_targets'], {})
        self.assertEqual(len(self.pending), 1)

    def test_rooftop_player_can_spawn_enemy_on_visible_ground_below(self):
        self.owner.position.y = 25
        def collision(space, start, end):
            vertical = start[0] == end[0] and start[2] == end[2]
            if vertical and end[1] <= 0 <= start[1]:
                return (Vector(start[0], 0, start[2]),)
            return None
        self.engine.collide = collision
        self.install()
        self.pending.pop(0)()
        self.assertEqual(self.entities[10].position.y, 0.02)
        self.assertEqual(self.state['local_round']['spawned'], 1)

    def test_obscured_ground_does_not_stall_finite_round_forever(self):
        def collision(space, start, end):
            if start[0] == end[0] and start[2] == end[2]:
                return (Vector(start[0], 0, start[2]),)
            return (Vector(1, 1, 1),)
        self.engine.collide = collision
        self.install()
        self.pending.pop(0)()
        self.assertEqual(self.state['local_round']['spawned'], 1)


if __name__ == '__main__':
    unittest.main()
