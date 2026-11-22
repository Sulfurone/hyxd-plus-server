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
        self.itemListContainer = SimpleNamespace(insertItem=self.insert_bag,
                                                 countTotalWrapById=self.count_bag)
        self.loadAmmo = Mock(side_effect=self.load)
        self.loadAmmo.im_func = SimpleNamespace(func_globals={
            'Item': Item, 'combatconst': SimpleNamespace(
                EQUIPMENT_SUBSLOT_BODY=0, EQUIPMENT_SUBSLOT_AMMO=7,
                LIFECODE_DEAD=2,
                COMBINED_EQUIPMENT_ID_PROP_MAP={1: 'equipMain', 2: 'equipSecondary'})})

    def insert_bag(self, item):
        self.bag[item.itemId] = self.bag.get(item.itemId, 0) + item.cwrap

    def count_bag(self, item_id):
        return self.bag.get(item_id, 0)

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
            gun.getItemData = lambda: {'hitDamage': 4200, 'partDamage': {1: 2.4, 5: 1}}
            self.guns.append(gun)
        self.player.equipWeapons = {gun.slotId: gun for gun in self.guns}
        self.commands = SimpleNamespace(beginAttackWithRayWeapon=Mock(),
            beginAttackWithDestributeWeapon=Mock(), reloadActiveWeapon=Mock(),
            attackWithRayWeapon=Mock(), attackWithDestributeWeapon=Mock(),
            utils=SimpleNamespace(getHitId=lambda encoded: encoded >> 5))
        self.original_fire = self.commands.beginAttackWithRayWeapon
        self.original_reload = self.commands.reloadActiveWeapon
        self.global_state = SimpleNamespace(isOffline=True)
        self.entities = {}
        self.callbacks = Mock()
        self.state = {}
        modules = {'BigWorld': SimpleNamespace(player=lambda: self.player,
                       entities=self.entities, callback=self.callbacks),
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
        self.bridge.install(self.player, self.state, lambda message: None)

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

    def target(self):
        target = SimpleNamespace(hp=10000, lifeCode=0, set_hp=Mock(), set_lifeCode=Mock())
        target.isDead = lambda: target.lifeCode == 2
        self.entities[42] = target
        self.state['local_targets'] = {42: {}}
        return target

    def test_body_hits_use_weapon_damage_and_trigger_native_death_once(self):
        target = self.target()
        for expected in (5800, 1600, 0):
            self.commands.attackWithRayWeapon(0, (), (), (), 1, 42, 5 << 5, ())
            self.assertEqual(target.hp, expected)
        self.assertTrue(target.isDead())
        target.set_lifeCode.assert_called_once_with(0)
        self.assertFalse(self.commands.attackWithRayWeapon(0, (), (), (), 1, 42, 5 << 5, ()))
        self.callbacks.assert_called_once()

    def test_head_damage_and_distributed_hits_use_native_hit_reports(self):
        target = self.target()
        self.commands.attackWithDestributeWeapon(0, (), (), [()], 1, [42], [1 << 5], [()])
        self.assertEqual(target.hp, 0)

    def test_unregistered_entities_do_not_take_local_target_damage(self):
        target = self.target()
        self.state['local_targets'].clear()
        self.assertFalse(self.commands.attackWithRayWeapon(0, (), (), (), 1, 42, 5 << 5, ()))
        self.assertEqual(target.hp, 10000)


if __name__ == '__main__':
    unittest.main()
