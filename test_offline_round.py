"""Verify round limits, safe-zone damage, results and restart isolation."""
import importlib.util
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_offline_combat import Inventory, Item


class Position:
    x, y, z = 0, 0, 0

    def __iter__(self):
        return iter((self.x, self.y, self.z))


class CircleEffect:
    def __init__(self):
        self.bCEffect = None
        self.fetch = Mock()

    def fetchCallback(self, success):
        return self.fetch(success)


class ResultProxy:
    def __init__(self):
        self.hide, self.show, self.exp = Mock(), Mock(), Mock()
        self.widget = SimpleNamespace(expMc=SimpleNamespace(visible=True))
        self.resultInfo = {}

    def setExpInfo(self, init=1):
        return self.exp(init)


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        constants = self.inventory.loadAmmo.im_func.func_globals['combatconst']
        constants.SINGLE_EQUIPMENT_ID_PROP_MAP = {}
        self.inventory.checkSum = 17
        self.inventory.insertItemToEquipment(Item(1, 10203, 1, {}), 1, 0)
        self.inventory.insertItemToEquipment(Item(2, 30102, 40, {}), 1, 7)
        self.inventory.bag[30102] = 0
        class Player:
            pass
        self.player = Player()
        self.player.__dict__.update(id=1, gbId=701, hp=10000, maxHp=10000,
            lifeCode=0, inWorld=True, physique=SimpleNamespace(sex=1),
            position=Position(), holdingEquipment=1, clientEquipsAndInv=self.inventory,
            equipWeapons={1: SimpleNamespace(isGun=True, slotId=1, bulletType=30102)},
            inParachuting=lambda: False, isOnPlane=lambda: False,
            isOnVehicle=lambda: False, isDead=lambda: self.player.lifeCode == 2,
            set_hp=Mock(), set_lifeCode=Mock(), onSyncAliveAvatarCnt=Mock(),
            syncSafeAreaShrinkInfo=Mock(), onLoadAmmo=Mock(),
            updateEquipmentsAndInvData=Mock(), refreshCurrentAmmo=Mock())
        self.player.syncSafeAreaInfo = Mock()
        self.player.syncSafeAreaInfo.im_func = SimpleNamespace(func_globals={
            'utils': SimpleNamespace(getTrueNow=lambda: self.now)})
        self.player.setVehiclePhysics = SimpleNamespace(im_func=SimpleNamespace(
            func_globals={'keys': SimpleNamespace(KEY_F10=10)}))
        self.player.handleKeyEvent = SimpleNamespace(im_func=Mock())
        self.now = 1000.0
        self.engine = SimpleNamespace(entities={}, callback=Mock(), destroyEntity=Mock())
        self.ui = SimpleNamespace(matchResult=ResultProxy(),
            gasStatusBar=SimpleNamespace(reset=Mock(), hide=Mock(), gasCircle=CircleEffect()))
        self.state = {'respawn_local_player': Mock(side_effect=self.recover),
            'refresh_local_inventory': Mock(), 'start_transport_flight': Mock(return_value=True)}
        self.log = Mock()
        modules = {'BigWorld': self.engine, 'gameglobal': SimpleNamespace(
            isOffline=True, rds=SimpleNamespace(ui=self.ui))}
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        spec = importlib.util.spec_from_file_location('_round_test',
            Path(__file__).with_name('offline_round.py'))
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)
        self.time_patch = patch.object(self.bridge, 'time', SimpleNamespace(time=lambda: self.now))
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)
        self.bridge.install(self.player, self.state, self.log)

    def recover(self):
        self.player.hp = 10000
        self.player.lifeCode = 0
        return True

    def start(self, **kwargs):
        self.assertTrue(self.state['start_local_round'](transport=False, **kwargs))
        return self.state['local_round']

    def tick(self):
        self.state['tick_local_round'](False)
        for call in self.log.call_args_list:
            self.assertNotIn('failed', call.args[0])

    def test_shrink_interpolation_and_horizontal_distance(self):
        stage = (600, 350, 50, 40, 100, (0, 0), (80, -10))
        self.assertEqual(self.bridge.circle_at(stage, 0), 600)
        self.assertEqual(self.bridge.circle_at(stage, 70), 475)
        self.assertEqual(self.bridge.circle_at(stage, 1000), 350)
        self.assertEqual(self.bridge.center_at(stage, 70), (40, -5))
        self.assertFalse(self.bridge.outside((0, 600, 0), (0, 0), 10))
        self.assertTrue(self.bridge.outside((11, 0, 0), (0, 0), 10))

    def test_native_circle_uses_game_clock_and_shrink_argument_order(self):
        self.state['loot_spatial_grid'] = {(0, 0): [(1, {'position': (500, 25, -800)})]}
        current = self.start()
        outer = math.hypot(4000, 4000)
        self.tick()
        self.player.syncSafeAreaInfo.assert_called_once_with((0, 0), outer, (500, -800), 2560, 1160, 1)
        self.now += 205
        self.tick()
        self.player.syncSafeAreaShrinkInfo.assert_called_once_with(
            (0, 0), outer, (500, -800), 2560, (outer - 2560) / 90, 1160, 1250, 1)
        self.assertAlmostEqual(current['radius'], (outer + 2560) / 2)
        self.assertEqual(current['center'], (250, -400))

    def test_circles_use_map_bounds_and_stay_inside_preceding_circle(self):
        land = {(0, 0): [(1, {'position': (1230, 20, 900)}),
                         (2, {'position': (7600, 20, 3900)})]}
        stages = self.bridge.plan_circles((-2000, 3500, 6000, 4000), land, random.Random(1))
        self.assertEqual(stages[0][5], (1000, 1500))
        self.assertEqual(stages[0][6], (1230, 900))
        self.assertEqual(stages[0][1], 1280)
        self.assertEqual(stages[0][0], math.hypot(3000, 2000))
        for outer, inner, _, _, _, center, next_center in stages:
            shift = math.hypot(center[0] - next_center[0], center[1] - next_center[1])
            self.assertLessEqual(shift + inner, outer)
        for previous, following in zip(stages, stages[1:]):
            self.assertEqual((previous[1], previous[6]), (following[0], following[5]))

    def test_last_elimination_wins_exactly_once(self):
        current = self.start(total=2)
        self.state['local_targets'] = {10: {'round_id': current['id']}, 11: {'round_id': current['id']}}
        self.state['on_local_enemy_defeated'](10, True)
        self.assertEqual(current['phase'], 'active')
        self.state['on_local_enemy_defeated'](11, False)
        self.assertEqual(current['phase'], 'finished')
        self.assertTrue(current['won'])
        self.assertEqual(current['result']['member'][701]['killSum'], 1)
        self.assertEqual(current['result']['rank'], 1)
        self.assertFalse(self.state['finish_local_round'](False, 'late'))
        self.ui.matchResult.show.assert_called_once()

    def test_zone_kills_player_and_does_not_schedule_respawn(self):
        current = self.start()
        self.player.position.x = 6000
        self.player.hp = 50
        self.now += 1
        self.tick()
        self.assertEqual(self.player.hp, 0)
        self.assertTrue(self.player.isDead())
        self.assertEqual(current['reason'], 'safe_zone')
        self.assertFalse(current['won'])
        self.assertNotIn('player_respawn_at', self.state)

    def test_death_at_last_enemy_does_not_become_victory(self):
        current = self.start(total=1)
        self.player.position.x = 6000
        self.player.hp = 50
        enemy = SimpleNamespace(position=self.player.position, hp=50, set_hp=Mock())
        self.engine.entities[10] = enemy
        self.state['local_targets'] = {10: {'round_id': current['id']}}
        self.state['defeat_local_enemy'] = self.state['on_local_enemy_defeated']
        self.now += 1
        self.tick()
        self.assertFalse(current['won'])

    def test_round_restart_clears_enemies_and_old_death_timer(self):
        first = self.start()
        self.engine.entities[10] = object()
        self.state['local_targets'] = {10: {'round_id': first['id']}}
        self.state['player_respawn_at'] = 99
        self.state['finish_local_round'](False, 'test')
        second = self.start()
        self.assertNotEqual(first['id'], second['id'])
        self.engine.destroyEntity.assert_called_once_with(10)
        self.assertEqual(second['eliminated'], 0)
        self.assertEqual(self.state['local_targets'], {})
        self.assertNotIn('player_respawn_at', self.state)

    def test_transport_shows_circle_before_landing_and_keeps_its_schedule(self):
        self.state['start_transport_flight'].side_effect = lambda: self.state.update(
            local_flight={'phase': 'loading', 'route': {'duration': 200}}) or True
        self.assertTrue(self.state['start_local_round']())
        current = self.state['local_round']
        self.tick()
        self.player.syncSafeAreaInfo.assert_not_called()
        self.state['local_flight']['phase'] = 'onboard'
        self.now += 100
        self.player.position.x = 6000
        self.tick()
        self.assertEqual(current['phase'], 'deploying')
        self.assertEqual(self.player.hp, 10000)
        self.player.syncSafeAreaInfo.assert_called_once()
        self.assertEqual(self.player.syncSafeAreaInfo.call_args.args[-2], 1270)
        center = current['center']
        del self.state['local_flight']
        self.player.position.x = 1800
        self.tick()
        self.assertEqual(current['phase'], 'active')
        self.assertEqual(current['stage_started'], 1000)
        self.assertEqual(current['center'], center)
        self.player.syncSafeAreaInfo.assert_called_once()

    def test_late_enemy_death_from_old_round_is_ignored(self):
        current = self.start()
        self.state['local_targets'] = {10: {'round_id': current['id'] - 1}}
        self.state['on_local_enemy_defeated'](10, True)
        self.assertEqual(current['eliminated'], 0)

    def test_restart_does_not_multiply_reserve_ammunition(self):
        self.start()
        self.assertEqual(self.player.clientEquipsAndInv.bag[30102], 120)
        self.player.clientEquipsAndInv.bag[30102] = 3
        self.start()
        self.assertEqual(self.player.clientEquipsAndInv.bag[30102], 120)
        self.assertEqual(self.state['round_loadout'].bag[30102], 0)

    def test_damage_backlog_is_capped_after_window_pause(self):
        self.start()
        self.player.position.x = 6000
        self.now += 50
        self.tick()
        self.assertEqual(self.player.hp, 9900)

    def test_effect_fetch_after_reset_does_not_touch_destroyed_effect(self):
        circle = self.ui.gasStatusBar.gasCircle
        circle.fetchCallback(True)
        circle.fetch.assert_not_called()
        circle.bCEffect = object()
        circle.fetchCallback(True)
        circle.fetch.assert_called_once_with(True)

    def test_local_result_has_statistics_without_online_progression(self):
        current = self.start()
        self.state['finish_local_round'](True, 'test')
        proxy = self.ui.matchResult
        proxy.resultInfo = current['result']
        proxy.setExpInfo()
        self.assertFalse(proxy.widget.expMc.visible)
        proxy.exp.assert_not_called()
        self.assertEqual(self.player.lv, 1)
        self.assertEqual(self.player.expLimitOfDaily, 0)

    def test_other_result_keeps_original_progression_callback(self):
        self.ui.matchResult.setExpInfo(0)
        self.ui.matchResult.exp.assert_called_once_with(0)

    def test_each_stage_reaches_its_next_radius_and_final_circle_closes(self):
        current = self.start()
        stages = current['circles']
        for stage in stages:
            self.assertEqual(self.bridge.circle_at(stage, stage[2] + stage[3]), stage[1])
            self.assertEqual(self.bridge.center_at(stage, stage[2] + stage[3]), stage[6])
        self.now += sum(stage[2] + stage[3] for stage in stages) + 1
        self.tick()
        self.assertEqual(current['radius'], 0)
        self.assertEqual(self.player.syncSafeAreaShrinkInfo.call_args.args[-1], len(stages))

    def test_airborne_restart_does_not_replace_current_round_or_inventory(self):
        current = self.start()
        inventory = self.player.clientEquipsAndInv
        self.player.inParachuting = lambda: True
        self.assertFalse(self.state['start_local_round']())
        self.assertIs(self.state['local_round'], current)
        self.assertIs(self.player.clientEquipsAndInv, inventory)


if __name__ == '__main__':
    unittest.main()
