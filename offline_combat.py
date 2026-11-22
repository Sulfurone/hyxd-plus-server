"""Local combat bridge for the original Python 2 client."""
import sys
import uuid

import BigWorld
import gameglobal


def install(player, state, log):
    weapons = getattr(player, 'equipWeapons', {})
    inventory = player.clientEquipsAndInv
    native = inventory.loadAmmo.im_func.func_globals
    constants = native['combatconst']
    Item = native['Item']

    def item(item_id, count=1, slots=None):
        return Item(uuid.uuid4().hex, item_id, count, slots or {})

    # Use the original inventory objects, so the reload animation's cue can
    # transfer real ammunition rather than updating a separate HUD counter.
    for key, weapon in weapons.items():
        if weapon is None or not getattr(weapon, 'isGun', False):
            continue
        slot = weapon.slotId
        if inventory.getItemInEquipment(slot, constants.EQUIPMENT_SUBSLOT_BODY) is None:
            prop = constants.COMBINED_EQUIPMENT_ID_PROP_MAP[slot]
            appearance = getattr(player.battleAspect, prop)
            parts = {index: item(part_id) for index, part_id in enumerate(appearance)
                     if index != constants.EQUIPMENT_SUBSLOT_BODY and part_id}
            inventory.insertItemToEquipment(item(weapon.id, slots=parts), slot,
                                            constants.EQUIPMENT_SUBSLOT_BODY)
            capacity = inventory.calcAmmoCapacity(slot)
            inventory.insertItemToEquipment(item(weapon.bulletType, capacity), slot,
                                            constants.EQUIPMENT_SUBSLOT_AMMO)
            inventory.itemListContainer.insertItem(item(weapon.bulletType, capacity * 5))
            log('Local ammunition initialized slot=%r gun=%r capacity=%r reserve=%r' % (
                slot, weapon.id, capacity, capacity * 5))

    gun_module = next((module for name, module in sys.modules.items()
                       if name.lower().endswith('weapon.weapongun')), None)
    if gun_module is None:
        raise RuntimeError('Original weapon module is not loaded')
    gun_cls = gun_module.WeaponBaseGun
    for name in ('bulletCnt', 'bulletMaxCnt', 'bulletInBag'):
        saved = '_hypOriginal_' + name
        original = getattr(gun_cls, saved, getattr(gun_cls, name))
        setattr(gun_cls, saved, original)

        def ammo_value(weapon, name=name, original=original):
            if not gameglobal.isOffline or weapon.owner is not BigWorld.player():
                return original.fget(weapon)
            if name == 'bulletCnt':
                ammo = weapon.owner.clientEquipsAndInv.getItemInEquipment(
                    weapon.slotId, constants.EQUIPMENT_SUBSLOT_AMMO)
                return ammo.cwrap if ammo is not None else 0
            if name == 'bulletMaxCnt':
                return weapon.owner.clientEquipsAndInv.calcAmmoCapacity(weapon.slotId)
            return weapon.owner.getBulletCntInBag(weapon.bulletType)

        setattr(gun_cls, name, property(ammo_value, original.fset, original.fdel))

    command_module = next((module for name, module in sys.modules.items()
                           if name.lower().endswith('helpers.servercmd')), None)
    if command_module is None:
        raise RuntimeError('Original server command module is not loaded')

    for name in ('beginAttackWithRayWeapon', 'beginAttackWithDestributeWeapon'):
        original = getattr(command_module, '_hypOriginal_' + name,
                           getattr(command_module, name))
        setattr(command_module, '_hypOriginal_' + name, original)

        def fire(now, start, ends, slot, original=original):
            if not gameglobal.isOffline:
                return original(now, start, ends, slot)
            owner = BigWorld.player()
            inv = owner.clientEquipsAndInv
            result = inv.reduceAmmoAfterShoot(slot, 1)
            owner.refreshCurrentAmmo()
            ammo = inv.getItemInEquipment(slot, constants.EQUIPMENT_SUBSLOT_AMMO)
            log('Local shot slot=%r remaining=%r accepted=%r' % (
                slot, ammo.cwrap if ammo is not None else 0, result))
            return result

        setattr(command_module, name, fire)

    original_reload = getattr(command_module, '_hypOriginalReloadActiveWeapon',
                              command_module.reloadActiveWeapon)
    command_module._hypOriginalReloadActiveWeapon = original_reload

    def reload_active_weapon(slotId=None):
        if not gameglobal.isOffline:
            return original_reload(slotId)
        owner = BigWorld.player()
        slot = owner.getActiveWeaponSlotId() if slotId is None else slotId
        result = owner.clientEquipsAndInv.loadAmmo(slot, uuid.uuid4().hex)
        owner.onLoadAmmo(result, slot)
        log('Local reload completed slot=%r loaded=%r' % (slot, result))
        return result

    command_module.reloadActiveWeapon = reload_active_weapon
    player.refreshCurrentAmmo()
    log('Local combat ready guns=%r' % [
        (weapon.slotId, weapon.bulletCnt, weapon.bulletMaxCnt, weapon.bulletInBag)
        for weapon in weapons.values() if weapon is not None and getattr(weapon, 'isGun', False)])
