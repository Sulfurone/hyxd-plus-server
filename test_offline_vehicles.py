"""Regression checks against the actual client-side vehicle adapter functions."""
import ast
import math
from pathlib import Path
import random
import time
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class VehicleTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path(__file__).with_name('offline_world.py').read_text())
        install = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                       and node.name == 'install_vehicles')
        functions = [node for node in install.body if isinstance(node, ast.FunctionDef)]
        self.owner = SimpleNamespace(id=77, position=(0, 0, 0), spaceID=1, inWorld=True,
            inParachuting=lambda: False, clientVehicleInfo={'vEntId': 42, 'vSeatNum': 0})
        self.vehicle = SimpleNamespace(id=42, inWorld=True, vTypeId=3,
            vPassengers=[77, 0, 0, 0, 0], position=(0, 0, 1),
            set_vPassengers=Mock(), onLeaveVehicle=Mock(), onSwitchVehicleSeat=Mock())
        self.entities = {42: self.vehicle}
        self.registry = {42: {}}
        self.engine = SimpleNamespace(player=lambda: self.owner, entities=self.entities,
            entity=lambda eid: self.entities.get(eid), createEntity=Mock(return_value=55),
            callback=Mock(), dcursor=lambda: SimpleNamespace(yaw=0),
            collide=Mock(return_value=None), destroyEntity=Mock())
        self.scope = dict(BigWorld=self.engine, gameglobal=SimpleNamespace(isOffline=True),
            Math=SimpleNamespace(Vector3=lambda *xyz: xyz), vd={
                3: {'seatNum': 5, 'wheelNum': 4, 'wheelDuration': 8000, 'duration': 220000},
                5: {'seatNum': 2, 'wheelNum': 4, 'wheelDuration': 8000, 'duration': 120000}},
            vehicles=self.registry, state={'vehicle_generation': 1, 'vehicle_limit': 1}, generation=1,
            original_enter=Mock(), original_leave=Mock(), original_switch=Mock(),
            log=Mock(), math=math, time=time, random=random, traceback=traceback, land_types=[3, 5])
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'vehicle_adapter', 'exec'), self.scope)

    def test_spawn_uses_native_seats_wheels_and_durability(self):
        self.scope['spawn_vehicle'](3, (0, 1, 0), 0)
        props = self.engine.createEntity.call_args.args[-1]
        self.assertEqual(props['vPassengers'], [0] * 5)
        self.assertEqual(props['wheelDurations'], [8000] * 4)
        self.assertEqual(props['hp'], 220000)
        self.assertIn(55, self.registry)

    def test_two_seat_vehicle_has_two_entries(self):
        self.scope['spawn_vehicle'](5, (0, 1, 0), 0)
        self.assertEqual(self.engine.createEntity.call_args.args[-1]['vPassengers'], [0, 0])

    def test_pending_model_load_does_not_trigger_repeated_spawning(self):
        self.vehicle.inWorld = False
        self.scope['refresh_vehicles']()
        self.assertIn(42, self.registry)
        self.engine.createEntity.assert_not_called()
        self.engine.callback.assert_called_once()

    def test_leave_reads_native_dictionary_and_calls_vehicle_callback(self):
        self.assertTrue(self.scope['v_apply_leave']((2, 0, 0)))
        self.assertEqual(self.vehicle.vPassengers, [0] * 5)
        self.vehicle.onLeaveVehicle.assert_called_once_with(77, 0, (2, 0, 0))

    def test_switch_seat_updates_via_vehicle_callback(self):
        self.assertTrue(self.scope['v_apply_switch'](self.owner, 1))
        self.assertEqual(self.vehicle.vPassengers, [0, 77, 0, 0, 0])
        self.vehicle.onSwitchVehicleSeat.assert_called_once_with(77, 0, 1)

    def test_invalid_seat_does_not_change_occupancy(self):
        self.assertFalse(self.scope['v_apply_switch'](self.owner, -1))
        self.assertEqual(self.vehicle.vPassengers, [77, 0, 0, 0, 0])
        self.vehicle.onSwitchVehicleSeat.assert_not_called()

    def test_stalled_empty_vehicle_is_removed_after_load_timeout(self):
        self.vehicle.inWorld = False
        self.vehicle.vPassengers = [0] * 5
        self.registry[42]['created_at'] = time.time() - 31
        self.scope['refresh_vehicles']()
        self.engine.destroyEntity.assert_called_once_with(42)
        self.assertNotIn(42, self.registry)


if __name__ == '__main__':
    unittest.main()
