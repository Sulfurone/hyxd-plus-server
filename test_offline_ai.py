"""Exercise local damage and native robot callback adapters."""
import ast
import math
from pathlib import Path
import time
import traceback
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class Vector:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __sub__(self, other):
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)


class Matrix:
    def setRotateY(self, yaw):
        self.yaw = yaw


class AITests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path(__file__).with_name('offline_ai.py').read_text())
        install = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'install')
        damage = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'damage_after_armor')
        functions = [damage] + [n for n in install.body if isinstance(n, ast.FunctionDef)]
        self.owner = SimpleNamespace(id=77, spaceID=1, hp=10000, maxHp=10000, lifeCode=0,
            equipWeapons={}, holdingEquipment=1,
            inWorld=True, inParachuting=lambda: False, isOnPlane=lambda: False,
            isOnVehicle=lambda: False, position=Vector(0, 0, 0),
            set_hp=Mock(), set_lifeCode=Mock(), playBeHittedEffect=Mock(),
            tryChangeToNormalMove=Mock(), ap=SimpleNamespace(resetControl=Mock()),
            physics=SimpleNamespace(teleport=Mock()))
        self.owner.isDead = lambda: self.owner.lifeCode == 3
        self.inventory = SimpleNamespace(getHeadDamageReduce=lambda: (0.5, 9000, (1, 2)),
            getBodyDamageReduce=lambda: (0.3, 10000, (5, 6, 9)),
            reduceEquipDuration=Mock(), checkSum=123)
        self.owner.clientEquipsAndInv = self.inventory
        self.owner.updateEquipmentsAndInvData = Mock()
        info = {'ai_ready': True, 'ready': True, 'motion_matrix': Matrix(), 'ammo': 5}
        self.state = {'ai_generation': 1, 'ai_enabled': True, 'local_targets': {42: info},
            'battle_alive_code': 0, 'battle_deaths': 0, 'battle_checkpoint': (1, 0, 1),
            'last_ai_spawn_check': time.time(), 'checkpoint_updated': time.time()}
        class Gun:
            @property
            def bulletCnt(gun):
                return info['ammo']
            def getItemData(gun):
                return {'hitDamage': 4200, 'partDamage': {1: 2.4, 5: 1, 7: 0.45}}
        self.gun = Gun()
        self.robot = SimpleNamespace(id=42, hp=10000, inWorld=True,
            getActivedWeapon=lambda: self.gun, position=Vector(0, 0, 10),
            onClientAIFireTimerRobot=Mock())
        self.engine = SimpleNamespace(player=lambda: self.owner,
            entities={42: self.robot}, callback=Mock(), collide=Mock(return_value=None))
        self.online_hit = Mock()
        globals_dict = {'gametypes': SimpleNamespace(RIGIDBODY_NODE_NAME_DICT={
            'head': 1, 'spine': 10, 'arm': 7}), 'utils': SimpleNamespace(
                setHitId=lambda part, front: part << 5,
                getHitId=lambda encoded: 5 if encoded >> 5 == 10 else encoded >> 5)}
        self.original_hit = SimpleNamespace(im_func=SimpleNamespace(func_globals=globals_dict))
        class CallableNative:
            im_func = self.original_hit.im_func
            def __call__(native, *args):
                return self.online_hit(*args)
        self.globals = SimpleNamespace(isOffline=True, rds=SimpleNamespace(
            ui=SimpleNamespace(pressKeyF=SimpleNamespace(show=Mock()))))
        self.scope = dict(BigWorld=self.engine, gameglobal=self.globals,
            Math=SimpleNamespace(Vector3=Vector), state=self.state,
            constants=SimpleNamespace(EQUIPMENT_SLOT_ID_HEAD=51, EQUIPMENT_SLOT_ID_VEST=52, LIFECODE_DEAD=3),
            original_hit=CallableNative(), original_begin=Mock(), original_awareness=Mock(), log=Mock(),
            math=math, time=time, traceback=traceback, generation=1)
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'ai_adapter', 'exec'), self.scope)

    def hit(self, node='spine', target=77):
        return self.scope['hit'](self.robot, (), (), (), target, node, '', 0, False)

    def test_native_body_codes_map_to_weapon_damage_and_armor(self):
        self.assertTrue(self.hit())
        self.assertEqual(self.owner.hp, 7060)
        self.inventory.reduceEquipDuration.assert_called_once_with(52, 2940, False)

    def test_unprotected_limb_does_not_spend_vest_durability(self):
        self.hit('arm')
        self.assertEqual(self.owner.hp, 8110)
        self.inventory.reduceEquipDuration.assert_not_called()

    def test_head_armor_reduces_head_multiplier(self):
        self.hit('head')
        self.assertEqual(self.owner.hp, 4960)

    def test_death_triggers_native_life_change_and_one_respawn_deadline(self):
        self.owner.hp = 1000
        self.hit()
        self.assertTrue(self.owner.isDead())
        self.assertEqual(self.state['battle_deaths'], 1)
        self.assertIn('player_respawn_at', self.state)
        self.assertFalse(self.hit())
        self.assertEqual(self.state['battle_deaths'], 1)

    def test_respawn_restores_native_control_and_protects_against_queued_bullets(self):
        self.owner.hp = 0
        self.owner.lifeCode = 3
        self.scope['respawn']()
        self.assertEqual(self.owner.hp, 10000)
        self.assertFalse(self.owner.isDead())
        self.owner.ap.resetControl.assert_called_once()
        self.assertFalse(self.hit())
        self.assertIs(self.owner.clientEquipsAndInv, self.inventory)

    def test_round_death_finishes_match_without_practice_respawn(self):
        self.state['local_round'] = {'phase': 'active'}
        self.state['finish_local_round'] = Mock()
        self.owner.hp = 1000
        self.hit()
        self.state['finish_local_round'].assert_called_once_with(False, 'bullet')
        self.assertNotIn('player_respawn_at', self.state)

    def test_active_round_cannot_use_practice_recovery(self):
        self.state['local_round'] = {'phase': 'active'}
        self.owner.hp = 2000
        self.assertFalse(self.scope['respawn']())
        self.assertEqual(self.owner.hp, 2000)

    def test_finished_round_stops_native_ai(self):
        self.state['local_round'] = {'phase': 'finished'}
        self.scope['update']()
        self.robot.onClientAIFireTimerRobot.assert_not_called()

    def test_native_shot_consumes_one_round(self):
        self.scope['begin_fire'](self.robot, 0, (), [(), (), ()], 1)
        self.assertEqual(self.state['local_targets'][42]['ammo'], 4)

    def test_wall_blocks_native_ai_firing(self):
        self.engine.collide.return_value = object()
        self.scope['update']()
        self.robot.onClientAIFireTimerRobot.assert_not_called()
        self.scope['log'].assert_not_called()

    def test_visible_target_runs_original_ai_fire_logic(self):
        self.scope['update']()
        self.robot.onClientAIFireTimerRobot.assert_called_once()
        self.scope['log'].assert_not_called()

    def test_pause_stops_firing_and_damage(self):
        self.state['ai_enabled'] = False
        self.scope['update']()
        self.robot.onClientAIFireTimerRobot.assert_not_called()
        self.assertFalse(self.hit())

    def test_destroyed_armor_provides_no_reduction(self):
        self.assertEqual(self.scope['damage_after_armor'](4200, 1, (0.5, 0, (5,))), 4200)

    def test_local_shooting_does_not_send_awareness_to_missing_robot_cell(self):
        self.scope['shot_awareness'](self.owner, (), (), 0)
        self.scope['original_awareness'].assert_not_called()
        self.assertIn('next_fire', self.state['local_targets'][42])

    def test_online_shot_awareness_keeps_original_callback(self):
        self.globals.isOffline = False
        self.scope['shot_awareness'](self.owner, (1,), (2,), 3)
        self.scope['original_awareness'].assert_called_once_with(self.owner, (1,), (2,), 3)

    def test_cold_start_retries_until_entity_modules_load(self):
        tree = ast.parse(Path(__file__).with_name('offline_ai.py').read_text())
        install = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'install')
        scope = dict(self.scope, sys=SimpleNamespace(modules={}))
        exec(compile(ast.Module(body=[copy.deepcopy(install)], type_ignores=[]), 'ai_startup', 'exec'), scope)
        scope['install'](self.owner, self.state, scope['log'])
        self.assertIn('ai_wait_since', self.state)
        self.assertEqual(self.engine.callback.call_args.args[0], 0.5)


if __name__ == '__main__':
    unittest.main()
