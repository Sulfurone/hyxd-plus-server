"""Exercise the client bridge against the inventory/server-command contract."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class Item:
    def __init__(self, uuid, item_id, count, slots):
        self.uuid, self.itemId, self.cwrap, self.slots = uuid, item_id, count, slots


class Inventory:
    def __init__(self):
        self.items, self.bag = {}, {}
        self.itemListContainer = SimpleNamespace(insertItem=self.insert_bag)
        self.loadAmmo = Mock(side_effect=self.load)
        self.loadAmmo.im_func = SimpleNamespace(func_globals={
            'Item': Item, 'combatconst': SimpleNamespace(
                EQUIPMENT_SUBSLOT_BODY=0, EQUIPMENT_SUBSLOT_AMMO=7,
                COMBINED_EQUIPMENT_ID_PROP_MAP={1: 'equipMain', 2: 'equipSecondary'})})

    def insert_bag(self, item):
        self.bag[item.itemId] = self.bag.get(item.itemId, 0) + item.cwrap

    def getItemInEquipment(self, slot, subslot):
        return self.items.get((slot, subslot))

    def insertItemToEquipment(self, item, slot, subslot):
        self.items[slot, subslot] = item

    def calcAmmoCapacity(self, slot):
        # Native inventory decides capacity, including attachments.
        return {1: 40, 2: 5}[slot]

    def reduceAmmoAfterShoot(self, slot, count):
        ammo = self.items.get((slot, 7))
        if not ammo or ammo.cwrap < count:
            return False
        ammo.cwrap -= count
        return True

    def load(self, slot, uuid):
        ammo = self.items[slot, 7]
        amount = min(self.calcAmmoCapacity(slot) - ammo.cwrap, self.bag[ammo.itemId])
        self.bag[ammo.itemId] -= amount
        ammo.cwrap += amount
        return ammo.cwrap


class CombatBridgeTests(unittest.TestCase):
    def setUp(self):
        class Gun:
            isGun = True
            bulletCnt = property(lambda self: 1)
            bulletMaxCnt = property(lambda self: 30)
            bulletInBag = property(lambda self: 30)

        self.inventory = Inventory()
        self.player = SimpleNamespace(
            clientEquipsAndInv=self.inventory,
            battleAspect=SimpleNamespace(equipMain=(10203, 20803), equipSecondary=(10103,)),
            refreshCurrentAmmo=Mock(), onLoadAmmo=Mock(),
            getActiveWeaponSlotId=lambda: 1,
            getBulletCntInBag=lambda ammo: self.inventory.bag.get(ammo, 0))
        self.guns = []
        for slot, gun_id, ammo in ((1, 10203, 30102), (2, 10103, 30103)):
            gun = Gun()
            gun.slotId, gun.id, gun.bulletType, gun.owner = slot, gun_id, ammo, self.player
            self.guns.append(gun)
        self.player.equipWeapons = {gun.slotId: gun for gun in self.guns}
        self.commands = SimpleNamespace(beginAttackWithRayWeapon=Mock(),
            beginAttackWithDestributeWeapon=Mock(), reloadActiveWeapon=Mock())
        self.original_fire = self.commands.beginAttackWithRayWeapon
        self.original_reload = self.commands.reloadActiveWeapon
        self.global_state = SimpleNamespace(isOffline=True)
        modules = {'BigWorld': SimpleNamespace(player=lambda: self.player),
                   'gameglobal': self.global_state,
                   'weapon.weaponGun': SimpleNamespace(WeaponBaseGun=Gun),
                   'helpers.serverCmd': self.commands}
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        spec = importlib.util.spec_from_file_location('_combat_under_test',
            Path(__file__).with_name('offline_combat.py'))
        self.bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bridge)
        self.install()

    def install(self):
        self.bridge.install(self.player, {}, lambda message: None)

    def test_original_capacity_and_inventory_drive_display(self):
        self.assertEqual([(g.bulletCnt, g.bulletMaxCnt, g.bulletInBag) for g in self.guns],
                         [(40, 40, 200), (5, 5, 25)])
        self.assertIn(1, self.inventory.items[1, 0].slots)

    def test_multi_ray_shot_costs_one_round_and_empty_gun_stops(self):
        for _ in range(40):
            self.assertTrue(self.commands.beginAttackWithDestributeWeapon(0, (), [(), (), ()], 1))
        self.assertEqual(self.guns[0].bulletCnt, 0)
        self.assertFalse(self.commands.beginAttackWithRayWeapon(0, (), (), 1))
        self.assertEqual(self.guns[1].bulletCnt, 5)

    def test_reload_transfers_reserve_only_when_native_cue_calls_command(self):
        self.commands.beginAttackWithRayWeapon(0, (), (), 1)
        self.assertEqual((self.guns[0].bulletCnt, self.guns[0].bulletInBag), (39, 200))
        self.commands.reloadActiveWeapon()
        self.assertEqual((self.guns[0].bulletCnt, self.guns[0].bulletInBag), (40, 199))
        self.player.onLoadAmmo.assert_called_once_with(40, 1)

    def test_hot_reload_preserves_spent_ammunition_and_does_not_stack_hooks(self):
        self.commands.beginAttackWithRayWeapon(0, (), (), 1)
        self.install()
        self.commands.beginAttackWithRayWeapon(0, (), (), 1)
        self.assertEqual((self.guns[0].bulletCnt, self.guns[0].bulletInBag), (38, 200))

    def test_online_commands_and_properties_keep_original_behavior(self):
        self.global_state.isOffline = False
        self.commands.beginAttackWithRayWeapon(1, (2,), (3,), 1)
        self.commands.reloadActiveWeapon(2)
        self.original_fire.assert_called_once_with(1, (2,), (3,), 1)
        self.original_reload.assert_called_once_with(2)
        self.assertEqual(self.guns[0].bulletCnt, 1)


if __name__ == '__main__':
    unittest.main()
