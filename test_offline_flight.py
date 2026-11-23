"""Flight lifecycle checks, including native entity cleanup after departure."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class NativeMethod:
    def __init__(self, callback, globals_dict=None):
        self.callback = callback
        self.im_func = SimpleNamespace(func_globals=globals_dict or {})

    def __call__(self, *args):
        return self.callback(*args)


class Entities(dict):
    def get(self, key, default=None):
        if key is None:
            raise TypeError('Native entity IDs cannot be None')
        return super().get(key, default)


class Matrix:
    def setRotateY(self, yaw):
        self.yaw = yaw


class Model:
    sources = ('original_plane.model',)

    def __init__(self):
        self.motors = []
        self.position = (0, 600, 0)

    def addMotor(self, motor):
        self.motors.append(motor)

    def delMotor(self, motor):
        self.motors.remove(motor)


class FlightTests(unittest.TestCase):
    def setUp(self):
        class Owner:
            pass
        self.owner = Owner()
        self.owner.position = (1000, 0, 1000)
        self.owner.id, self.owner.spaceID = 77, 1
        self.owner.hp = 10000
        self.owner.yaw, self.owner.avatarStatus = 0, 5
        self.owner.inWorld, self.owner.isDoingAction, self.owner.climbCode = True, False, ''
        self.owner.isOnPlane = lambda: self.owner.avatarStatus == 2
        self.owner.inParachuting = lambda: self.owner.avatarStatus in (3, 4)
        self.owner.isOnVehicle = lambda: False
        self.owner.physics = SimpleNamespace(teleport=Mock())
        self.owner.initParachuteParams = Mock()
        self.owner.onPlaneBoarding = Mock()
        self.owner.modelServer = SimpleNamespace(leaveAttachModel=Mock())
        self.owner.model = Model()
        statuses = SimpleNamespace(AVATAR_STATUS_IN_COMBAT=5, AVATAR_STATUS_ONBOARD=2,
            AVATAR_STATUS_STARTPARACHUTING=3, PLANE_FUNCTION_PARACHUTING=1,
            AVATAR_STATUS_PATACHUTE_OPEN_SET=(4, 8))
        self.owner.refreshAvatarStatus = NativeMethod(Mock(), {'gametypes': statuses})
        self.commands = SimpleNamespace(startParachuting=Mock())
        self.owner.pickUse = NativeMethod(Mock(), {'serverCmd': self.commands})
        self.owner.setVehiclePhysics = NativeMethod(Mock(), {'keys': SimpleNamespace(KEY_F9=120)})
        self.owner.handleKeyEvent = NativeMethod(Mock())
        self.entities = Entities()
        self.pending = []

        def create(*args):
            self.entities[42] = SimpleNamespace(id=42, model=Model(), firstModelFinish=True,
                                               setPlanePath=Mock(), position=args[3])
            return 42

        self.camera = SimpleNamespace(position=(1000, 600, 1000))
        self.clientcom = SimpleNamespace(getPlaneEntity=Mock(return_value=None))
        ui = SimpleNamespace(littleMap=SimpleNamespace(refreshAirline=NativeMethod(
            Mock(), {'clientcom': self.clientcom})))
        self.engine = SimpleNamespace(player=lambda: self.owner, entities=self.entities,
            createEntity=Mock(side_effect=create), destroyEntity=lambda eid: self.entities.pop(eid, None),
            dcursor=lambda: SimpleNamespace(yaw=0), camera=lambda: self.camera,
            callback=lambda delay, callback: self.pending.append((delay, callback)),
            Servo=lambda matrix: SimpleNamespace(target=matrix))
        patcher = patch.dict(sys.modules, {'BigWorld': self.engine,
            'Math': SimpleNamespace(Matrix=Matrix, Vector3=lambda *xyz: xyz),
            'gameglobal': SimpleNamespace(isOffline=True, rds=SimpleNamespace(ui=ui))})
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location('_flight_test', Path(__file__).with_name('offline_flight.py'))
        self.flight = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.flight)
        self.now = 1000
        self.flight.time = SimpleNamespace(time=lambda: self.now)
        self.state = {'transport_audit_version': 4}
        self.log = Mock()
        self.flight.install(self.owner, self.state, self.log)
        self.update = self.pending[0][1]

    def board(self):
        self.assertTrue(self.state['start_transport_flight']())
        self.update()
        self.assertEqual(self.state['local_flight']['phase'], 'onboard')
        self.owner.position = self.state['local_flight']['route']['source']

    def depart(self):
        self.board()
        self.now += 5
        self.assertTrue(self.commands.startParachuting())

    def test_route_interpolation_is_time_based_and_clamped(self):
        route = {'source': (0, 600, 0), 'destination': (600, 600, 0), 'duration': 10}
        self.assertEqual(self.flight.route_at(route, -1), (0, 600, 0))
        self.assertEqual(self.flight.route_at(route, 5), (300, 600, 0))
        self.assertEqual(self.flight.route_at(route, 20), (600, 600, 0))

    def test_boarding_uses_original_plane_and_shared_native_follow_matrix(self):
        self.board()
        self.assertEqual(self.engine.createEntity.call_args.args[0], 'Plane')
        self.owner.onPlaneBoarding.assert_called_once_with(42)
        self.assertIs(self.owner.physics.followTarget, self.state['local_flight']['matrix'])
        self.assertEqual(len(self.owner.planeRouteData), 4)

    def test_departure_is_gated_and_detaches_boarding_state(self):
        self.board()
        self.assertFalse(self.commands.startParachuting())
        self.now += 5
        self.assertTrue(self.commands.startParachuting())
        self.assertEqual(self.owner.avatarStatus, 3)
        self.assertEqual(self.owner.boardingPlaneId, 0)

    def test_plane_stays_in_flight_after_player_jumps_and_lands(self):
        self.depart()
        self.now += 9
        self.update()
        self.assertIn(42, self.entities)
        self.assertEqual(self.state['local_flight']['phase'], 'parachuting')
        self.assertEqual(self.owner.avatarStatus, 3)
        self.owner.avatarStatus = 5
        self.update()
        self.assertNotIn('local_flight', self.state)
        self.assertIn(42, self.entities)
        departing = self.state['departing_transport']
        self.now = departing['started'] + departing['route']['duration'] + 1
        self.update()
        self.assertEqual(departing['matrix'].translation, departing['route']['destination'])
        self.assertNotIn('departing_transport', self.state)
        self.assertNotIn(42, self.entities)
        self.update()

    def test_native_route_starts_and_ends_at_opposite_edges_regardless_of_spawn(self):
        self.board()
        route = self.state['local_flight']['route']
        self.assertEqual(route['source'], (0, 600, -3840))
        self.assertEqual(route['destination'], (0, 600, 3840))
        self.assertEqual(self.owner.planeRouteData[0]['pos'], route['source'])
        self.assertEqual(self.owner.planeRouteData[-1]['pos'], route['destination'])

    def test_original_map_lookup_tracks_moving_transform_not_plane_spawn(self):
        self.board()
        route = self.state['local_flight']['route']
        plane = self.entities[42]
        plane.setPlanePath.assert_called_once_with()
        self.assertEqual(self.clientcom.getPlaneEntity().position, route['source'])
        self.now += 5
        self.owner.position = self.flight.route_at(route, 5)
        self.update()
        view = self.clientcom.getPlaneEntity()
        self.assertEqual(view.id, plane.id)
        self.assertEqual(view.position, self.owner.position)
        self.assertNotEqual(view.position, plane.position)
        self.assertTrue(self.commands.startParachuting())
        self.owner.avatarStatus = 5
        self.update()
        self.assertEqual(self.clientcom.getPlaneEntity().position, self.owner.position)
        self.now += route['duration']
        self.update()
        self.assertIsNone(self.clientcom.getPlaneEntity())

    def test_diagonal_route_honors_a_rectangular_map_with_an_offset(self):
        import math
        route = self.flight.map_route((100, 1000, 1200, 400), math.pi / 4, 600)
        self.assertAlmostEqual(route['source'][0], 540)
        self.assertAlmostEqual(route['source'][2], 640)
        self.assertAlmostEqual(route['destination'][0], 860)
        self.assertAlmostEqual(route['destination'][2], 960)

    def test_failed_native_follow_aborts_and_restores_ground_position(self):
        self.board()
        self.owner.position = (1000, 0, 1000)
        self.now += 3
        self.update()
        self.assertNotIn('local_flight', self.state)
        self.assertNotIn(42, self.entities)
        self.assertEqual(self.owner.avatarStatus, 5)
        self.owner.physics.teleport.assert_called_once_with((1000, 0, 1000))

    def test_missing_plane_model_cannot_leave_player_onboard(self):
        self.state['start_transport_flight']()
        self.entities[42].model = None
        self.now += 21
        self.update()
        self.assertNotIn('local_flight', self.state)
        self.assertEqual(self.owner.avatarStatus, 5)

    def test_normal_install_does_not_start_an_unrequested_flight(self):
        self.update()
        self.engine.createEntity.assert_not_called()
        self.assertNotIn('local_flight', self.state)

    def test_f9_creates_one_flight_even_when_key_repeats(self):
        handler = type(self.owner).handleKeyEvent
        self.assertTrue(handler(self.owner, True, 120, 0))
        self.assertTrue(handler(self.owner, True, 120, 0))
        self.assertTrue(handler(self.owner, False, 120, 0))
        self.engine.createEntity.assert_called_once()

    def test_dead_player_cannot_board_a_new_flight(self):
        self.owner.hp = 0
        self.assertFalse(self.state['start_transport_flight']())
        self.engine.createEntity.assert_not_called()


if __name__ == '__main__':
    unittest.main()
