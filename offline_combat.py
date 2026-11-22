"""Local combat bridge for the original Python 2 client."""
import sys
import uuid
import copy

import BigWorld
import gameglobal


def install(player, state, log):
    weapons = getattr(player, 'equipWeapons', {})
    inventory = player.clientEquipsAndInv
    native = inventory.loadAmmo.im_func.func_globals
    constants = native['combatconst']
    Item = native['Item']
    if not state.get('damage_data_logged'):
        for weapon in weapons.values():
            if weapon is not None and getattr(weapon, 'isGun', False):
                log('Local gun damage configuration id=%r data=%r' % (
                    weapon.id, {key: value for key, value in weapon.getItemData().items()
                     if any(word in key.lower() for word in ('damage', 'hurt', 'power', 'hit'))}))
        state['damage_data_logged'] = True

    def item(item_id, count=1, slots=None):
        return Item(int(uuid.uuid4().hex[:7], 16), item_id, count, slots or {})

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
            if state.get('battle_ray_audit'):
                log('Native player audit ray start=%r ends=%r camera=%r direction=%r dcYaw=%r dcPitch=%r' % (
                    start, ends, BigWorld.camera().position, BigWorld.camera().direction,
                    BigWorld.dcursor().yaw, BigWorld.dcursor().pitch))
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
        result = owner.clientEquipsAndInv.loadAmmo(slot, int(uuid.uuid4().hex[:7], 16))
        owner.onLoadAmmo(result, slot)
        log('Local reload completed slot=%r loaded=%r' % (slot, result))
        return result

    command_module.reloadActiveWeapon = reload_active_weapon

    def apply_hit(slot, entity_id, encoded_part):
        targets = state.get('local_targets', {})
        entry = targets.get(entity_id)
        target = BigWorld.entities.get(entity_id)
        owner = BigWorld.player()
        if entry is None or target is None or target.hp <= 0:
            return False
        weapon = next((weapon for weapon in owner.equipWeapons.values()
                       if weapon is not None and getattr(weapon, 'slotId', None) == slot), None)
        if weapon is None:
            return False
        data = weapon.getItemData()
        part = command_module.utils.getHitId(encoded_part)
        damage = int(round(data.get('hitDamage', 0) * data.get('partDamage', {}).get(part, 1)))
        old_hp = target.hp
        target.hp = max(0, old_hp - damage)
        target.set_hp(old_hp)
        log('Local bullet hit id=%r slot=%r part=%r damage=%r hp=%r->%r' % (
            entity_id, slot, part, damage, old_hp, target.hp))
        if target.hp == 0:
            state['battle_kills'] = state.get('battle_kills', 0) + 1
            old_life = target.lifeCode
            target.lifeCode = constants.LIFECODE_DEAD
            target.set_lifeCode(old_life)
            log('Local target defeated id=%r nativeDeath=%r' % (entity_id, target.isDead()))

            def replace_target():
                if BigWorld.entities.get(entity_id) is not None:
                    BigWorld.destroyEntity(entity_id)
                targets.pop(entity_id, None)
                scope = {}
                execfile('I:/hyxd/hyp/_local_server/offline_targets.py', scope)
                scope['install'](BigWorld.player(), state, log)

            BigWorld.callback(8, replace_target)
        return True

    for name in ('attackWithRayWeapon', 'attackWithDestributeWeapon'):
        original = getattr(command_module, '_hypOriginal_' + name,
                           getattr(command_module, name))
        setattr(command_module, '_hypOriginal_' + name, original)

        def hit(now, start, direction, end, slot, entity_ids, body_parts, positions,
                original=original, distributed=(name == 'attackWithDestributeWeapon')):
            if not gameglobal.isOffline:
                return original(now, start, direction, end, slot, entity_ids, body_parts, positions)
            if distributed:
                return [apply_hit(slot, entity_id, part)
                        for entity_id, part in zip(entity_ids, body_parts)]
            return apply_hit(slot, entity_ids, body_parts)

        setattr(command_module, name, hit)

    if not state.get('native_ammo_audited'):
        # Verify the extracted client's real inventory methods on a copy.
        # This neither fires the player's weapon nor spends their ammunition.
        audit = copy.deepcopy(inventory)
        for weapon in weapons.values():
            if weapon is None or not getattr(weapon, 'isGun', False):
                continue
            slot = weapon.slotId
            ammo = audit.getItemInEquipment(slot, constants.EQUIPMENT_SUBSLOT_AMMO)
            if ammo is None or ammo.cwrap <= 0:
                continue
            before = ammo.cwrap
            reserve = audit.itemListContainer.countTotalWrapById(weapon.bulletType)
            audit.reduceAmmoAfterShoot(slot, 1)
            after = audit.getItemInEquipment(slot, constants.EQUIPMENT_SUBSLOT_AMMO)
            assert (after.cwrap if after is not None else 0) == before - 1
            audit.loadAmmo(slot, int(uuid.uuid4().hex[:7], 16))
            after = audit.getItemInEquipment(slot, constants.EQUIPMENT_SUBSLOT_AMMO)
            left = audit.itemListContainer.countTotalWrapById(weapon.bulletType)
            assert after.cwrap + left == before + reserve - 1
            log('Native ammunition audit slot=%r before=%r afterReload=%r reserve=%r conservation=OK' % (
                slot, before, after.cwrap, left))
        state['native_ammo_audited'] = True
    player.refreshCurrentAmmo()
    log('Local combat ready guns=%r' % [
        (weapon.slotId, weapon.bulletCnt, weapon.bulletMaxCnt, weapon.bulletInBag)
        for weapon in weapons.values() if weapon is not None and getattr(weapon, 'isGun', False)])
