"""Regression checks for asynchronous dropping and local loot lifecycle."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class Vector:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    def __iter__(self):
        return iter((self.x, self.y, self.z))


class NativeMethod:
    def __init__(self, globals_dict, callback):
        self.im_func = SimpleNamespace(func_globals=globals_dict)
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.owner = SimpleNamespace(position=Vector(0, 0, 0), spaceID=1, inWorld=True,
            avatarStatus=5, climbCode='', initParachuteParams=Mock(),
            physics=SimpleNamespace(teleport=Mock()))
        types = SimpleNamespace(AVATAR_STATUS_STARTPARACHUTING=3)
        self.owner.inParachuting = lambda: self.owner.avatarStatus in (3, 4)
        self.refresh = Mock()
        self.owner.refreshAvatarStatus = NativeMethod({'gametypes': types}, self.refresh)
        self.bw = SimpleNamespace(player=lambda: self.owner,
            collide=Mock(return_value=(Vector(0, 0, 0),)),
            callback=lambda delay, callback: self.pending.append((delay, callback)))
        self.module_patch = patch.dict(sys.modules, {'BigWorld': self.bw,
            'Math': SimpleNamespace(Vector3=Vector), 'gameglobal': SimpleNamespace(isOffline=True,
                rds=SimpleNamespace(ui=SimpleNamespace(inventory=SimpleNamespace(widget=None, refreshInfo=Mock()))))})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        spec = importlib.util.spec_from_file_location('_world_under_test',
            Path(__file__).with_name('offline_world.py'))
        self.world = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.world)
        self.state, self.log = {}, Mock()

    def test_drop_waits_for_engine_to_apply_teleport_before_running_detection(self):
        self.world.start_drop(self.owner, self.state, self.log)
        self.assertEqual(self.owner.avatarStatus, 5)
        self.pending.pop(0)[1]()
        self.assertEqual(self.owner.avatarStatus, 5)
        self.owner.position.y = 599
        self.pending.pop(0)[1]()
        self.assertEqual(self.owner.avatarStatus, 3)
        self.refresh.assert_called_once_with(5)
        self.assertFalse(self.state['drop_pending'])

    def test_repeated_key_does_not_start_multiple_pending_drops(self):
        self.world.start_drop(self.owner, self.state, self.log)
        self.world.start_drop(self.owner, self.state, self.log)
        self.owner.physics.teleport.assert_called_once()

    def test_missing_ground_does_not_move_player(self):
        self.bw.collide.return_value = None
        self.world.start_drop(self.owner, self.state, self.log)
        self.owner.physics.teleport.assert_not_called()

    def prepare_loot(self):
        class Item:
            def __init__(self, uid, item_id, count, slots):
                self.uuid, self.itemId, self.cwrap = uid, item_id, count
                self.slots = slots

        self.entities, self.bag, self.bag_items = {}, {}, {}
        self.fake_next = 0

        def create(cls, position, direction, props):
            self.fake_next += 1
            self.entities[self.fake_next] = SimpleNamespace(**props)
            return self.fake_next

        def insert(item):
            self.bag[item.itemId] = self.bag.get(item.itemId, 0) + item.cwrap
            self.bag_items[item.uuid] = item

        def remove(uid, count):
            it = self.bag_items[uid]
            self.bag[it.itemId] -= count
            it.cwrap -= count
            if it.cwrap == 0:
                del self.bag_items[uid]

        fake = SimpleNamespace(createFakeEntity=create,
            destroyFakeEntity=lambda eid: self.entities.pop(eid, None),
            entity=lambda eid: self.entities.get(eid))
        self.drop_module = SimpleNamespace(FakeEntity=fake, DroppedItem=object,
            IHRIDD=SimpleNamespace(data={1: {'position': (1, 0, 0)}}))
        self.commands = SimpleNamespace(**{name: Mock() for name in
            ('pickItemToInvFromGround', 'pickItemToInvFromItemHeap',
             'pickItemToEquipmentFromGround', 'pickItemToEquipmentFromItemHeap',
             'discardItemToGroundFromInv', 'discardItemToGroundFromEquipment')})
        self.world.find_module = lambda name: self.commands if 'servercmd' in name else self.drop_module
        inv = SimpleNamespace(loadAmmo=NativeMethod({'Item': Item, 'combatconst': object()}, Mock()),
            calcMaxNumCanInsertItemToInv=lambda item_id: 1000,
            insertItemToInv=insert, checkSum=123,
            itemListContainer=SimpleNamespace(itemList=[]), _modifiedInvIds=set(),
            equipments={},
            getItemInInv=lambda uid: self.bag_items.get(uid), removeInvItem=remove,
            getInvWeight=lambda: 0, getWeightLimit=lambda: 1000)
        self.state['starter_backpack_initialized'] = True
        self.bw.dcursor = lambda: SimpleNamespace(yaw=0)
        self.owner.clientEquipsAndInv = inv
        self.owner.refreshCurrentAmmo = Mock()
        self.owner.updateEquipmentsAndInvData = Mock()
        self.clock = 1000
        self.world.time = SimpleNamespace(time=lambda: self.clock)
        self.world.install_loot(self.owner, self.state, self.log)
        self.pending.pop(0)[1]()
        self.owner.updateEquipmentsAndInvData.reset_mock()

    def test_loot_uses_native_models_and_pickup_updates_inventory(self):
        self.prepare_loot()
        uid, entry = next(iter(self.state['local_loot'].items()))
        self.assertEqual(self.entities[entry['fake_id']].slots, {})
        self.assertTrue(self.commands.pickItemToInvFromGround((), uid, entry['count']))
        self.assertEqual(self.bag[entry['item_id']], entry['initial_count'])
        self.assertIsNone(entry['fake_id'])
        self.owner.updateEquipmentsAndInvData.assert_called_once_with([], 123)

    def test_item_identifiers_round_trip_through_inventory_ui_numbers(self):
        self.prepare_loot()
        for uid in self.state['local_loot']:
            self.assertIsInstance(uid, int)
            self.assertLess(uid, 2 ** 30)
            self.assertEqual(uid, int(float(uid)))
        uid, entry = next(iter(self.state['local_loot'].items()))
        self.assertTrue(self.commands.pickItemToInvFromGround((), int(float(uid)), entry['count']))

    def test_legacy_uuid_values_migrate_into_windows_python_int_range(self):
        for old in ('a42bc901cc11ff229ac58ff327740106', 393335206705284):
            new = self.world.ui_uuid(old)
            self.assertIsInstance(new, int)
            self.assertLess(new, 2 ** 30)
            self.assertEqual(self.world.ui_uuid(new), new)

    def test_picked_loot_stays_absent_until_refresh_deadline(self):
        self.prepare_loot()
        uid, entry = next(iter(self.state['local_loot'].items()))
        self.commands.pickItemToInvFromGround((), uid, entry['count'])
        self.pending.pop(0)[1]()
        self.assertIsNone(entry['fake_id'])
        self.clock += 121
        self.pending.pop(0)[1]()
        self.assertIsNotNone(entry['fake_id'])
        self.assertEqual(entry['count'], entry['initial_count'])

    def test_out_of_range_pickup_is_rejected(self):
        self.prepare_loot()
        uid, entry = next(iter(self.state['local_loot'].items()))
        self.owner.position = Vector(100, 0, 0)
        self.assertFalse(self.commands.pickItemToInvFromGround((), uid, entry['count']))
        self.assertEqual(self.bag, {})

    def test_discard_and_pickup_conserve_item_count(self):
        self.prepare_loot()
        uid, entry = next(iter(self.state['local_loot'].items()))
        self.commands.pickItemToInvFromGround((), uid, entry['count'])
        original = entry['initial_count']
        self.assertTrue(self.commands.discardItemToGroundFromInv(uid, original))
        self.assertEqual(self.bag[entry['item_id']], 0)
        dropped_uid, dropped = next((uid, item) for uid, item in self.state['local_loot'].items() if item.get('dropped'))
        self.assertTrue(self.commands.pickItemToInvFromGround((), dropped_uid, original))
        self.assertEqual(self.bag[entry['item_id']], original)
        self.assertEqual(dropped['count'], 0)

    def test_full_inventory_does_not_remove_ground_item(self):
        self.prepare_loot()
        self.owner.clientEquipsAndInv.calcMaxNumCanInsertItemToInv = lambda item_id: 0
        uid, entry = next(iter(self.state['local_loot'].items()))
        self.assertFalse(self.commands.pickItemToInvFromGround((), uid, entry['count']))
        self.assertIsNotNone(entry['fake_id'])
        self.assertEqual(self.bag, {})


if __name__ == '__main__':
    unittest.main()
