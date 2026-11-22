"""Medicine casts must drive native animation and consume only on completion."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class MedicineTests(unittest.TestCase):
    def setUp(self):
        self.pending, self.state, self.calls = [], {}, []
        self.item = SimpleNamespace(uuid=12, itemId=50102, cwrap=2)
        self.player = SimpleNamespace(id=77, yaw=1.2, hp=3000, maxHp=10000,
            power=0, position=(0, 0, 0), inWorld=True, isDoingAction=False,
            inParachuting=lambda: False, useItemOfDrugChecker=lambda item: True,
            set_hp=Mock(), set_power=Mock())
        self.player.useItemBeginSpell = lambda *args: self.calls.append(('begin_item', args))
        self.player.useItemEndSpell = lambda *args: self.calls.append(('end_item', args))

        def start(*args):
            self.calls.append(('start_action', args))
            self.player.isDoingAction = True

        def end(*args):
            self.calls.append(('end_action', args))
            self.player.isDoingAction = False

        self.player.startActionProgress = Mock(side_effect=start)
        self.player.startActionProgress.im_func = SimpleNamespace(func_globals={
            'const': SimpleNamespace(ACT_USING_ITEM=5)})
        self.player.endActionProgress = Mock(side_effect=end)
        self.player.clientEquipsAndInv = SimpleNamespace(
            getItemInInv=lambda uid: self.item if uid == self.item.uuid and self.item.cwrap else None,
            removeInvItem=lambda uid, count: setattr(self.item, 'cwrap', self.item.cwrap - count))
        self.commands = SimpleNamespace(useCommonItem=Mock(), cancelAction=Mock())
        self.original_use = self.commands.useCommonItem
        self.original_cancel = self.commands.cancelAction
        self.globals = SimpleNamespace(isOffline=True)
        patcher = patch.dict(sys.modules, {
            'gameglobal': self.globals,
            'BigWorld': SimpleNamespace(player=lambda: self.player,
                callback=lambda delay, func: self.pending.append(func))})
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location('_medicine_test',
            Path(__file__).with_name('offline_medicine.py'))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.now = 1000
        self.module.time = SimpleNamespace(time=lambda: self.now)
        self.drugs = {50102: {'csmItemAction': 50102, 'spellTime': 6,
                            'hpRecoveryImmediately': 6000, 'maxRecovery': 10000}}
        self.refresh = Mock()
        self.install()

    def install(self):
        self.module.install(self.player, self.state, Mock(), self.commands, self.drugs, self.refresh)

    def test_cast_stows_weapon_and_starts_native_action_and_bar(self):
        self.assertTrue(self.commands.useCommonItem(12, 1))
        self.player.startActionProgress.assert_called_once_with(6, 50102, 77, True, 1.2, 5)
        self.assertEqual(self.item.cwrap, 2)
        self.assertEqual(self.player.hp, 3000)

    def test_success_finishes_animation_restores_weapon_and_consumes_once(self):
        self.commands.useCommonItem(12, 1)
        self.now += 6
        callback = self.pending.pop(0)
        callback()
        callback()
        self.assertEqual(self.player.hp, 9000)
        self.assertEqual(self.item.cwrap, 1)
        self.player.endActionProgress.assert_called_once_with(True, 50102)
        self.assertEqual(self.calls[-2:], [('end_action', (True, 50102)), ('end_item', (True, 12, 1))])
        self.refresh.assert_called_once_with(self.player)

    def test_cancel_stops_animation_bar_and_keeps_item(self):
        self.commands.useCommonItem(12, 1)
        self.assertTrue(self.commands.cancelAction(9))
        self.now += 10
        self.pending.pop(0)()
        self.player.endActionProgress.assert_called_once_with(False, 50102)
        self.assertEqual(self.calls[-1], ('end_item', (False, 12, 1)))
        self.assertEqual(self.item.cwrap, 2)
        self.assertEqual(self.player.hp, 3000)

    def test_movement_cancels_before_healing(self):
        self.commands.useCommonItem(12, 1)
        self.player.position = (0.3, 0, 0)
        self.pending.pop(0)()
        self.assertNotIn('active_spell_token', self.state)
        self.player.endActionProgress.assert_called_once_with(False, 50102)
        self.assertEqual(self.item.cwrap, 2)

    def test_hot_reload_invalidates_old_completion(self):
        self.commands.useCommonItem(12, 1)
        old_callback = self.pending.pop(0)
        self.install()
        self.now += 10
        old_callback()
        self.assertEqual(self.item.cwrap, 2)
        self.assertEqual(self.player.hp, 3000)
        self.assertFalse(self.player.isDoingAction)

    def test_begin_failure_cleans_up_started_item_ui(self):
        self.player.startActionProgress.side_effect = RuntimeError('missing animation')
        self.assertFalse(self.commands.useCommonItem(12, 1))
        self.assertNotIn('active_spell_token', self.state)
        self.assertEqual(self.calls[-1], ('end_item', (False, 12, 1)))

    def test_online_calls_use_original_commands(self):
        self.globals.isOffline = False
        self.commands.useCommonItem(12, 1)
        self.commands.cancelAction(4)
        self.original_use.assert_called_once_with(12, 1)
        self.original_cancel.assert_called_once_with(4)
        self.player.startActionProgress.assert_not_called()


if __name__ == '__main__':
    unittest.main()
