"""Local parachuting and map item adapters for the original client."""
import sys
import time
import math
import random
import uuid
import traceback
import copy

import BigWorld
import Math
import gameglobal


def find_module(suffix):
    return next((module for name, module in sys.modules.items()
                 if name.lower().endswith(suffix.lower())), None)


def item_uuid():
    # Windows Python 2 uses a 32-bit C long. Larger IDs become PyLong and the
    # original Scaleform bridge serializes those as strings (area headings).
    return int(uuid.uuid4().hex[:7], 16)


def ui_uuid(value):
    if isinstance(value, str):
        value = int(value[:13], 16)
    return int(value % 0x3fffffff)


def set_status(owner, status, log):
    old = owner.avatarStatus
    if old != status:
        owner.avatarStatus = status
        owner.refreshAvatarStatus(old)
        log('Local parachute status=%r->%r position=%r' % (old, status, owner.position))


def start_drop(owner, state, log):
    types = owner.refreshAvatarStatus.im_func.func_globals['gametypes']
    if (owner.hp <= 0 or owner.isOnVehicle() or owner.inParachuting() or owner.isOnPlane()
            or getattr(owner, 'climbCode', '') or state.get('drop_pending')):
        return
    position = owner.position
    hit = BigWorld.collide(owner.spaceID, (position.x, position.y + 2, position.z),
                           (position.x, -1000, position.z))
    if hit is None:
        log('Local parachute start deferred: ground geometry unavailable')
        return
    height = hit[0].y + 600
    state['drop_pending'] = True
    owner.physics.teleport(Math.Vector3(position.x, height, position.z))

    def begin(attempt=0):
        if owner.position.y < height - 100:
            if attempt < 10:
                BigWorld.callback(0.1, lambda: begin(attempt + 1))
            else:
                state['drop_pending'] = False
                log('Local parachute start failed: high altitude teleport was not applied')
            return
        state['drop_pending'] = False
        owner.initParachuteParams()
        state['local_drop_started'] = time.time()
        set_status(owner, types.AVATAR_STATUS_STARTPARACHUTING, log)

    BigWorld.callback(0.1, begin)


def install_parachute(player, state, log):
    commands = find_module('helpers.servercmd')
    types = player.refreshAvatarStatus.im_func.func_globals['gametypes']
    statuses = {'startParachuting': types.AVATAR_STATUS_STARTPARACHUTING,
                'startParachutingInCombat': types.AVATAR_STATUS_PARACHUTING_IN_COMBAT,
                'startOpenParachut': types.AVATAR_STATUS_PARACHUTE_OPEN,
                'startLanding': types.AVATAR_STATUS_IN_COMBAT}
    for name, status in statuses.items():
        original = getattr(commands, '_hypOriginal_' + name, getattr(commands, name))
        setattr(commands, '_hypOriginal_' + name, original)

        def change(status=status, original=original, name=name):
            if not gameglobal.isOffline:
                return original()
            owner = BigWorld.player()
            if name == 'startOpenParachut':
                if owner.avatarStatus == types.AVATAR_STATUS_PARACHUTING_IN_COMBAT:
                    status = types.AVATAR_STATUS_PATACHUTE_OPEN_IN_COMBAT
                elif owner.avatarStatus not in types.AVATAR_STATUS_STARTPARACHUTING_SET:
                    return
            set_status(owner, status, log)

        setattr(commands, name, change)

    original = getattr(type(player), '_hypOriginalWorldHandleKeyEvent',
                       player.handleKeyEvent.im_func)
    type(player)._hypOriginalWorldHandleKeyEvent = original
    key_module = original.func_globals.get('keys')
    drop_key = getattr(key_module, 'KEY_F8', None)
    if drop_key is not None:
        def handle_key(owner, is_down, key, mods):
            if gameglobal.isOffline and key == drop_key and is_down:
                try:
                    start_drop(owner, state, log)
                except Exception:
                    log('Local parachute start failed: %s' % traceback.format_exc())
                return True
            return original(owner, is_down, key, mods)
        type(player).handleKeyEvent = handle_key
        log('Local parachute hotkey ready: F8')
    else:
        log('Local parachute key API=%r' % [name for name in dir(key_module) if 'F8' in name])


def install_loot(player, state, log):
    module = find_module('helpers.droppeditemmanager')
    commands = find_module('helpers.servercmd')
    if module is None:
        raise RuntimeError('Original dropped item manager is not loaded')
    fake = module.FakeEntity
    inventory = player.clientEquipsAndInv
    native = inventory.loadAmmo.im_func.func_globals
    Item, constants = native['Item'], native['combatconst']
    if state.get('consumable_probe') != 1:
        pick_globals = player.useItemOfDrugChecker.im_func.func_globals
        drug_data = pick_globals['IDD'].data
        ui_utils = module.DroppedItem.getPickUIInfo.im_func.func_globals['uiUtils']
        log('Local drug definitions=%r' % [(item_id, ui_utils.getItemNameById(item_id), data)
            for item_id, data in sorted(drug_data.items())[:12]])
        log('Local armor definitions=%r' % [(item_id, ui_utils.getItemNameById(item_id), data)
            for item_id, data in sorted(native['ID'].data.items())
            if any(slot in data.get('slotId', ()) for slot in (51, 52)) and data.get('level') in (1, 2)][:10])
        log('Local medicine APIs=%r' % [name for name in dir(player)
            if any(word in name.lower() for word in ('spell', 'commonitem', 'usingitem', 'restorehp'))])
        state['consumable_probe'] = 1
    def migrate_item(it):
        if it is None:
            return
        it.uuid = ui_uuid(it.uuid)
        for part in getattr(it, 'slots', {}).values():
            migrate_item(part)

    for it in inventory.itemListContainer.itemList:
        migrate_item(it)
    for equipment in inventory.equipments.values():
        migrate_item(getattr(equipment, '_bodyItem', None))
        for it in getattr(equipment, '_slots', {}).values():
            migrate_item(it)
    if not state.get('starter_backpack_initialized'):
        if inventory.getItemInEquipment(53, constants.EQUIPMENT_SUBSLOT_BODY) is None:
            inventory.insertItemToEquipment(Item(item_uuid(), 70101, 1, {}),
                                            53, constants.EQUIPMENT_SUBSLOT_BODY)
            player.battleAspect.equipBag = 70101
            player.set_battleAspect(None)
        state['starter_backpack_initialized'] = True
    inventory._modifiedInvIds.update(it.itemId for it in inventory.itemListContainer.itemList)
    player.updateEquipmentsAndInvData([], inventory.checkSum)
    log('Local inventory synchronized weight=%r limit=%r' % (
        inventory.getInvWeight(), inventory.getWeightLimit()))
    proxy = gameglobal.rds.ui.inventory
    proxy_cls = type(proxy)
    for label in ('inventoryViewLabelFunc', 'groundViewLabelFunc'):
        original = getattr(proxy_cls, '_hypOriginal_' + label, None)
        if original is not None:
            setattr(proxy_cls, label, getattr(original, 'im_func', original))
    if proxy.widget is not None:
        proxy.widget.inventoryPanel.inventoryView.labelFunction = proxy.inventoryViewLabelFunc
        proxy.widget.groundPanel.groundView.labelFunction = proxy.groundViewLabelFunc
    proxy.refreshInfo()
    records = state.setdefault('local_loot', {})
    if state.get('loot_choices_version') != 3:
        for old_uuid, entry in list(records.items()):
            if not entry.get('dropped'):
                if entry.get('fake_id') is not None:
                    fake.destroyFakeEntity(entry['fake_id'])
                del records[old_uuid]
        state['loot_choices_version'] = 3
    for old_uuid, entry in list(records.items()):
        new_uuid = ui_uuid(old_uuid)
        if new_uuid != old_uuid:
            if entry.get('fake_id') is not None:
                fake.destroyFakeEntity(entry['fake_id'])
                entry['fake_id'] = None
            if entry.get('item') is not None:
                migrate_item(entry['item'])
            records[new_uuid] = entry
            del records[old_uuid]
    for entry in records.values():
        entity = fake.entity(entry['fake_id']) if entry.get('fake_id') is not None else None
        if entity is not None and not hasattr(entity, 'slots'):
            fake.destroyFakeEntity(entry['fake_id'])
            entry['fake_id'] = None
    state['world_generation'] = state.get('world_generation', 0) + 1
    generation = state['world_generation']
    heaps = module.IHRIDD.data
    if 'loot_spatial_grid' not in state:
        grid = {}
        for heap_id, data in heaps.items():
            x, y, z = data['position']
            cell = (int(math.floor(x / 128)), int(math.floor(z / 128)))
            grid.setdefault(cell, []).append((heap_id, data))
        state['loot_spatial_grid'] = grid
    grid = state['loot_spatial_grid']
    item_defs = native['ID'].data
    drug_defs = player.useItemOfDrugChecker.im_func.func_globals['IDD'].data

    def init_item_durability(it):
        if it is None:
            return it
        item_data = item_defs.get(it.itemId, {})
        durable = item_data.get('durable', 0)
        if durable and not getattr(it, 'duration', 0):
            it.duration = durable
        return it

    for equipment in inventory.equipments.values():
        init_item_durability(getattr(equipment, '_bodyItem', None))
    for it in inventory.itemListContainer.itemList:
        init_item_durability(it)

    # Native weapons, ammo, attachments, medical items, armor, and backpacks.
    base_choices = [
        # Firearms
        (10101, 1), (10102, 1), (10103, 1), (10104, 1),
        (10201, 1), (10202, 1), (10203, 1), (10204, 1), (10206, 1), (10208, 1),
        (10301, 1), (10302, 1), (10305, 1),
        (10401, 1), (10403, 1),
        (10501, 1), (10502, 1), (10504, 1),
        (10601, 1),
        # Ammo
        (30101, 30), (30102, 30), (30103, 30), (30104, 10), (30105, 30), (30106, 10),
        # Medical & energy
        (50101, 5), (50102, 1), (50103, 1),
        (50201, 1), (50202, 1), (50203, 1),
        # Attachments (scopes, muzzles, mags, grips, stocks)
        (21001, 1), (21002, 1), (21101, 1), (21102, 1), (21103, 1), (21104, 1),
        (20303, 1), (20401, 1), (20402, 1), (20403, 1), (20501, 1), (20503, 1),
        (20703, 1), (20801, 1), (20802, 1), (20803, 1), (20902, 1), (20903, 1),
        (21401, 1), (21402, 1), (21201, 1), (21203, 1), (21301, 1),
        # Armor & backpacks
        (40101, 1), (40111, 1), (40202, 1), (40211, 1),
        (70101, 1), (70111, 1),
    ]
    for candidate_id in (40121, 40122, 40221, 40222, 70121):
        if candidate_id in item_defs:
            base_choices.append((candidate_id, 1))
    choices = tuple(pair for pair in base_choices
                    if pair[0] in item_defs or pair[0] in drug_defs)

    def spawn_record(item_uuid, entry, yaw=0):
        source = entry.get('item')
        props = {'uuid': item_uuid, 'itemId': entry['item_id'],
                 'cwrap': entry['count'], 'heapId': 0,
                 'slots': copy.deepcopy(source.slots) if source is not None else {}}
        if source is not None and hasattr(source, 'duration'):
            props['duration'] = source.duration
        elif entry['item_id'] in item_defs and item_defs[entry['item_id']].get('durable'):
            props['duration'] = item_defs[entry['item_id']]['durable']
        entry['fake_id'] = fake.createFakeEntity(module.DroppedItem,
            entry['position'], (0, yaw, 0), props)

    def drop_item(owner, it):
        new_uuid = item_uuid()
        it.uuid = new_uuid
        yaw = BigWorld.dcursor().yaw
        position = owner.position
        x, z = position.x + math.sin(yaw), position.z + math.cos(yaw)
        hit = BigWorld.collide(owner.spaceID, (x, position.y + 1, z),
                               (x, position.y - 3, z))
        ground = hit[0].y if hit is not None else position.y
        entry = {'item_id': it.itemId, 'count': it.cwrap, 'initial_count': it.cwrap,
                 'position': (x, ground + 0.05, z), 'item': it, 'dropped': True, 'fake_id': None}
        records[new_uuid] = entry
        spawn_record(new_uuid, entry, yaw)
        log('Local item dropped uuid=%r item=%r count=%r' % (new_uuid, it.itemId, it.cwrap))

    def refresh_inventory(owner, slot=None):
        inv = owner.clientEquipsAndInv
        if slot is not None:
            prop = constants.COMBINED_EQUIPMENT_ID_PROP_MAP.get(slot)
            if prop is None:
                prop = constants.SINGLE_EQUIPMENT_ID_PROP_MAP.get(slot)
            if prop is not None:
                setattr(owner.battleAspect, prop, inv.getBroadcastDataBySlotId(slot))
                owner.set_battleAspect(None)
        owner.updateEquipmentsAndInvData([], inv.checkSum)
        owner.refreshCurrentAmmo()

    state['refresh_local_inventory'] = refresh_inventory

    def pick(item_uuid, count=None, slot=None, subslot=None):
        entry = records.get(item_uuid)
        owner = BigWorld.player()
        if entry is None or entry.get('fake_id') is None:
            return False
        if sum((a - b) ** 2 for a, b in zip(entry['position'], owner.position)) > 36:
            return False
        inv = owner.clientEquipsAndInv
        amount = entry['count'] if count is None else min(int(count), entry['count'])
        if amount <= 0:
            return False
        it = copy.deepcopy(entry['item']) if entry.get('item') is not None else Item(item_uuid, entry['item_id'], amount, {})
        it.cwrap = amount
        init_item_durability(it)
        if slot is None:
            amount = min(amount, inv.calcMaxNumCanInsertItemToInv(entry['item_id']))
            if amount <= 0:
                return False
            it.cwrap = amount
            inv.insertItemToInv(it)
        else:
            if not inv.checkInsertToEquipmentByItem(owner.physique.sex, it, slot, subslot):
                return False
            old = inv.getItemInEquipment(slot, subslot)
            if old is not None:
                old, extra = inv.removeItemInEquipment(slot, subslot, False, False)
                if inv.checkInsertToInvByItem(old):
                    inv.insertItemToInv(old)
                else:
                    drop_item(owner, old)
                for extra_item in extra.values():
                    if inv.checkInsertToInvByItem(extra_item):
                        inv.insertItemToInv(extra_item)
                    else:
                        drop_item(owner, extra_item)
            inv.insertItemToEquipment(it, slot, subslot)
        entry['count'] -= amount
        if entry['count'] == 0:
            fake.destroyFakeEntity(entry['fake_id'])
            entry['fake_id'] = None
            entry['respawn_at'] = time.time() + 120
        else:
            entity = fake.entity(entry['fake_id'])
            if entity is not None:
                entity.updateCWrap(entry['count'])
        refresh_inventory(owner, slot)
        log('Local loot picked uuid=%r item=%r count=%r equipmentSlot=%r' % (
            item_uuid, entry['item_id'], amount, slot))
        return True

    for name in ('pickItemToInvFromGround', 'pickItemToInvFromItemHeap',
                 'pickItemToEquipmentFromGround', 'pickItemToEquipmentFromItemHeap',
                 'replaceItemToEquipmentFromGround', 'replaceItemToEquipmentFromItemHeap'):
        original = getattr(commands, '_hypOriginal_' + name, getattr(commands, name))
        setattr(commands, '_hypOriginal_' + name, original)

        def pickup(location, item_uuid, arg, subslot=None, original=original,
                   equip=('Equipment' in name)):
            if not gameglobal.isOffline:
                if equip:
                    return original(location, item_uuid, arg, subslot)
                return original(location, item_uuid, arg)
            try:
                return pick(item_uuid, slot=arg, subslot=subslot) if equip else pick(item_uuid, count=arg)
            except Exception:
                log('Local loot pickup failed: %s' % traceback.format_exc())
                return False

        setattr(commands, name, pickup)

    def equip_from_inv(item_uuid, slot, subslot, callback_id=0, replace_uuid=None):
        owner = BigWorld.player()
        inv = owner.clientEquipsAndInv
        it = inv.getItemInInv(item_uuid)
        if it is None:
            return False
        equip_it = copy.deepcopy(it)
        equip_it.cwrap = 1
        init_item_durability(equip_it)
        if not inv.checkInsertToEquipmentByItem(owner.physique.sex, equip_it, slot, subslot):
            return False
        inv.removeInvItem(item_uuid, 1)
        old = inv.getItemInEquipment(slot, subslot)
        if old is not None:
            old, extra = inv.removeItemInEquipment(slot, subslot, False, False)
            if inv.checkInsertToInvByItem(old):
                inv.insertItemToInv(old)
            else:
                drop_item(owner, old)
            for extra_item in extra.values():
                if inv.checkInsertToInvByItem(extra_item):
                    inv.insertItemToInv(extra_item)
                else:
                    drop_item(owner, extra_item)
        inv.insertItemToEquipment(equip_it, slot, subslot)
        refresh_inventory(owner, slot)
        if replace_uuid is not None and hasattr(owner, 'onReplaceItemToEquipmentFromInv'):
            owner.onReplaceItemToEquipmentFromInv(item_uuid, replace_uuid, slot, subslot, callback_id)
        elif hasattr(owner, 'onPickItemToEquipmentFromInv'):
            owner.onPickItemToEquipmentFromInv(item_uuid, slot, subslot, callback_id)
        log('Local equip from inv uuid=%r item=%r slot=%r subslot=%r' % (
            item_uuid, equip_it.itemId, slot, subslot))
        return True

    original_pick_eq = getattr(commands, '_hypOriginal_pickItemToEquipmentFromInv',
                               commands.pickItemToEquipmentFromInv)
    commands._hypOriginal_pickItemToEquipmentFromInv = original_pick_eq

    def pick_eq_from_inv(item_uuid, slotId, subSlotId, callbackId=0):
        if not gameglobal.isOffline:
            return original_pick_eq(item_uuid, slotId, subSlotId, callbackId)
        try:
            return equip_from_inv(item_uuid, slotId, subSlotId, callbackId)
        except Exception:
            log('Local equip from inv failed: %s' % traceback.format_exc())
            return False

    commands.pickItemToEquipmentFromInv = pick_eq_from_inv

    original_rep_eq = getattr(commands, '_hypOriginal_replaceItemToEquipmentFromInv',
                              commands.replaceItemToEquipmentFromInv)
    commands._hypOriginal_replaceItemToEquipmentFromInv = original_rep_eq

    def replace_eq_from_inv(item_uuid, slotId, subSlotId, uuidEquipment, callbackId=0):
        if not gameglobal.isOffline:
            return original_rep_eq(item_uuid, slotId, subSlotId, uuidEquipment, callbackId)
        try:
            return equip_from_inv(item_uuid, slotId, subSlotId, callbackId, replace_uuid=uuidEquipment)
        except Exception:
            log('Local replace from inv failed: %s' % traceback.format_exc())
            return False

    commands.replaceItemToEquipmentFromInv = replace_eq_from_inv

    original_unequip = getattr(commands, '_hypOriginal_discardItemToInvFromEquipment',
                               commands.discardItemToInvFromEquipment)
    commands._hypOriginal_discardItemToInvFromEquipment = original_unequip

    def discard_to_inv(item_uuid, slotId, subSlotId):
        if not gameglobal.isOffline:
            return original_unequip(item_uuid, slotId, subSlotId)
        try:
            owner = BigWorld.player()
            inv = owner.clientEquipsAndInv
            it = inv.getItemInEquipment(slotId, subSlotId)
            if it is None or it.uuid != item_uuid:
                return False
            if not inv.checkInsertToInvByItem(it):
                return False
            it, extra = inv.removeItemInEquipment(slotId, subSlotId, False, False)
            inv.insertItemToInv(it)
            for extra_item in extra.values():
                if inv.checkInsertToInvByItem(extra_item):
                    inv.insertItemToInv(extra_item)
                else:
                    drop_item(owner, extra_item)
            refresh_inventory(owner, slotId)
            if slotId == owner.getActiveWeaponSlotId() and subSlotId == constants.EQUIPMENT_SUBSLOT_BODY:
                owner.switchHoldingEquip(constants.HOLDING_EQUIPMENT_NONE, True, True)
            log('Local unequipped to inv uuid=%r item=%r slot=%r subslot=%r' % (
                item_uuid, it.itemId, slotId, subSlotId))
            return True
        except Exception:
            log('Local unequip to inv failed: %s' % traceback.format_exc())
            return False

    commands.discardItemToInvFromEquipment = discard_to_inv

    medicine_scope = {}
    execfile('I:/hyxd/hyp/_local_server/offline_medicine.py', medicine_scope)
    medicine_scope['install'](player, state, log, commands, drug_defs, refresh_inventory)

    def energy_tick():
        if state.get('world_generation') != generation:
            return
        try:
            owner = BigWorld.player()
            if (not state.get('lobby_active') and owner is not None and owner.inWorld and owner.hp > 0
                    and not owner.isDead() and hasattr(owner, 'power') and owner.power > 0):
                old_power = owner.power
                heal = 200 if old_power >= 6000 else 100
                if owner.hp < owner.maxHp:
                    old_hp = owner.hp
                    owner.hp = min(owner.maxHp, old_hp + heal)
                    if hasattr(owner, 'set_hp'):
                        owner.set_hp(old_hp)
                owner.power = max(0, old_power - 150)
                if hasattr(owner, 'set_power'):
                    owner.set_power(old_power)
        except Exception:
            pass
        BigWorld.callback(3.0, energy_tick)

    BigWorld.callback(3.0, energy_tick)

    for name in ('discardItemToGroundFromInv', 'discardItemToGroundFromEquipment'):
        original = getattr(commands, '_hypOriginal_' + name, getattr(commands, name))
        setattr(commands, '_hypOriginal_' + name, original)

        def discard(item_uuid, value, subslot=None, original=original,
                    equip=('Equipment' in name)):
            if not gameglobal.isOffline:
                return original(item_uuid, value, subslot) if equip else original(item_uuid, value)
            try:
                owner = BigWorld.player()
                inv = owner.clientEquipsAndInv
                log('Local discard requested uuid=%r value=%r subslot=%r equipment=%r' % (
                    item_uuid, value, subslot, equip))
                if equip:
                    it = inv.getItemInEquipment(value, subslot)
                    if it is None or it.uuid != item_uuid:
                        return False
                    it, extra = inv.removeItemInEquipment(value, subslot, False, False)
                    for extra_item in extra.values():
                        inv.insertItemToInv(extra_item)
                    drop_item(owner, it)
                    refresh_inventory(owner, value)
                    if value == owner.getActiveWeaponSlotId() and subslot == constants.EQUIPMENT_SUBSLOT_BODY:
                        owner.switchHoldingEquip(constants.HOLDING_EQUIPMENT_NONE, True, True)
                else:
                    it = inv.getItemInInv(item_uuid)
                    if it is None or int(value) <= 0:
                        log('Local discard rejected missingItem=%r count=%r' % (it is None, value))
                        return False
                    amount = min(int(value), it.cwrap)
                    dropped = copy.deepcopy(it)
                    dropped.cwrap = amount
                    inv.removeInvItem(item_uuid, amount)
                    drop_item(owner, dropped)
                    refresh_inventory(owner)
                return True
            except Exception:
                log('Local discard failed: %s' % traceback.format_exc())
                return False

        setattr(commands, name, discard)

    def update():
        if state.get('world_generation') != generation:
            return
        if state.get('lobby_active'):
            BigWorld.callback(1, update)
            return
        try:
            owner = BigWorld.player()
            if owner is None or not owner.inWorld:
                return
            position, now = owner.position, time.time()
            cell_x, cell_z = int(math.floor(position.x / 128)), int(math.floor(position.z / 128))
            candidates = [pair for dx in range(-2, 3) for dz in range(-2, 3)
                          for pair in grid.get((cell_x + dx, cell_z + dz), ())]
            nearby = sorted(((sum((a - b) ** 2 for a, b in zip(data['position'], position)), heap_id, data)
                             for heap_id, data in candidates), key=lambda row: row[0])[:24]
            if nearby and state.get('nearest_loot_heap') != nearby[0][1]:
                state['nearest_loot_heap'] = nearby[0][1]
                log('Local loot nearest heap=%r distance=%.1f position=%r' % (
                    nearby[0][1], math.sqrt(nearby[0][0]), nearby[0][2]['position']))
            for item_uuid, entry in records.items():
                distance = sum((a - b) ** 2 for a, b in zip(entry['position'], position))
                if entry.get('fake_id') is not None and distance > 102400:
                    fake.destroyFakeEntity(entry['fake_id'])
                    entry['fake_id'] = None
                elif entry.get('dropped') and entry['count'] > 0 and entry.get('fake_id') is None and distance < 62500:
                    spawn_record(item_uuid, entry)
            spawned = 0
            active = sum(entry.get('fake_id') is not None for entry in records.values())
            if active > 72:
                surplus = sorted((entry for entry in records.values() if entry.get('fake_id') is not None),
                    key=lambda entry: sum((a-b)**2 for a,b in zip(entry['position'], position)), reverse=True)
                for entry in surplus[:min(4, active - 72)]:
                    fake.destroyFakeEntity(entry['fake_id'])
                    entry['fake_id'] = None
                    active -= 1
            for distance, heap_id, data in nearby:
                if distance > 62500:
                    continue
                rng = random.Random(heap_id)
                for index in range(3):
                    item_uuid = ui_uuid(int(uuid.uuid5(uuid.NAMESPACE_DNS, 'hyp-local-loot-%s-%s' % (heap_id, index)).hex[:13], 16))
                    entry = records.get(item_uuid)
                    if entry is None:
                        item_id, count = choices[rng.randrange(len(choices))]
                        x, y, z = data['position']
                        entry = {'item_id': item_id, 'initial_count': count, 'count': count,
                                 'position': (x + (index - 1) * 0.45, y + 0.05, z), 'fake_id': None}
                        records[item_uuid] = entry
                    if entry.get('fake_id') is not None or entry.get('respawn_at', 0) > now:
                        continue
                    if active >= 72 or spawned >= 4:
                        continue
                    if entry['count'] <= 0:
                        entry['count'] = entry['initial_count']
                    spawn_record(item_uuid, entry, rng.random() * math.pi * 2)
                    spawned += 1
                    active += 1
            if spawned:
                log('Local map loot spawned=%r active=%r nearestHeapDistance=%.1f' % (
                    spawned, sum(entry.get('fake_id') is not None for entry in records.values()),
                    math.sqrt(nearby[0][0]) if nearby else -1))
        except Exception:
            log('Local loot refresh failed: %s' % traceback.format_exc())
        BigWorld.callback(0.5, update)

    BigWorld.callback(0.2, update)


def install_vehicles(player, state, log):
    commands = find_module('helpers.servercmd')
    veh_mod = find_module('vehicle')
    if commands is None or veh_mod is None or not hasattr(veh_mod, 'VD'):
        log('Local vehicles skipped: vehicle module unavailable')
        return
    vd = veh_mod.VD.data
    land_types = [vid for vid, vdata in sorted(vd.items())
                  if vdata.get('vehicleType') == 1 and vid >= 3]
    if not land_types:
        land_types = [vid for vid, vdata in sorted(vd.items())
                      if vdata.get('vehicleType') == 1] or [0]

    vehicles = state.setdefault('local_vehicles', {})
    actual = [entity for entity in BigWorld.entities.values() if type(entity).__name__ == 'Vehicle']
    log('Vehicle fleet audit tracked=%r actual=%r loaded=%r fps=%r' % (
        len(vehicles), len(actual), sum(entity.isVehicleLoadComplete() for entity in actual),
        BigWorld.getFps()))
    # Keep pending entities registered: asynchronous load is not destruction.
    for entity in actual:
        vehicles.setdefault(entity.id, {'vTypeId': entity.vTypeId, 'position': tuple(entity.position)})
        seats = max(1, int(vd.get(entity.vTypeId, {}).get('seatNum', 4)))
        old = list(entity.vPassengers)
        if len(old) != seats:
            entity.vPassengers = (old + [0] * seats)[:seats]
            log('Local vehicle seats repaired id=%r count=%r->%r' % (entity.id, len(old), seats))
        data = vd.get(entity.vTypeId, {})
        wheels = max(2, int(data.get('wheelNum', 4)))
        old_wheels = list(entity.wheelDurations)
        if len(old_wheels) != wheels:
            entity.wheelDurations = (old_wheels + [int(data.get('wheelDuration', 8000))] * wheels)[:wheels]
            log('Local vehicle wheel state repaired id=%r count=%r->%r' % (entity.id, len(old_wheels), wheels))
    # Retire surplus unoccupied physics vehicles left by the previous spawner.
    keep = sorted(actual, key=lambda entity: sum((a-b)**2 for a,b in zip(entity.position, player.position)))
    for entity in keep[1:]:
        if not any(entity.vPassengers):
            BigWorld.destroyEntity(entity.id)
            vehicles.pop(entity.id, None)
    state['vehicle_limit'] = 1
    if state.get('vehicle_spawn_version') != 5:
        for old_eid in list(vehicles):
            ent = BigWorld.entities.get(old_eid)
            if ent is not None and not any(getattr(ent, 'vPassengers', ()) or ()):
                BigWorld.destroyEntity(old_eid)
                vehicles.pop(old_eid, None)
        state['vehicle_spawn_version'] = 5
    state['vehicle_generation'] = state.get('vehicle_generation', 0) + 1
    generation = state['vehicle_generation']

    def get_seat_count(vdata):
        return max(1, int(vdata.get('seatNum', 4)))

    def spawn_vehicle(vid, pos, yaw):
        vdata = vd.get(vid, {})
        max_hp = int(vdata.get('duration', 220000))
        max_fuel = int(vdata.get('fuelMax', 1000000) or 1000000)
        wheels = vdata.get('wheels') or ()
        wheel_count = int(vdata.get('wheelNum', 4))
        seat_count = get_seat_count(vdata)
        props = {
            'vTypeId': vid,
            'originalTypeId': vid,
            'hp': max_hp,
            'maxHp': max_hp,
            'fuelNow': max_fuel,
            'vPassengers': [0] * seat_count,
            'vLastDriverId': 0,
            'wheelDurations': [int(vdata.get('wheelDuration', 8000))] * max(2, wheel_count),
            'skinId': 0,
            'airDropStatus': 1,
        }
        ent_id = BigWorld.createEntity('Vehicle', BigWorld.player().spaceID, 0,
                                       Math.Vector3(*pos), Math.Vector3(0, 0, yaw), props)
        if ent_id:
            vehicles[ent_id] = {'vTypeId': vid, 'position': pos, 'created_at': time.time()}
            log('Local vehicle spawned id=%r type=%r pos=%r' % (ent_id, vid, pos))
        return ent_id

    veh_cls = veh_mod.Vehicle
    editor = gameglobal.rds.ui.vehicleEditor
    editor_cls = type(editor)
    editor_show = getattr(editor_cls, '_hypOriginalLocalShow', editor_cls.show)
    editor_cls._hypOriginalLocalShow = editor_show

    def show_editor(view, vehicle):
        if gameglobal.isOffline and vehicle.id in state.get('local_vehicles', {}):
            return
        return editor_show(view, vehicle)

    editor_cls.show = show_editor
    if editor.widget is not None:
        editor.clearWidget()
    orig_pick_use = getattr(veh_cls, '_hypOriginal_pickUse', veh_cls.pickUse)
    veh_cls._hypOriginal_pickUse = orig_pick_use

    def diag_pick_use(veh_self, *args, **kwargs):
        owner = BigWorld.player()
        const_mod = orig_pick_use.im_func.func_globals.get('const')
        ct = getattr(const_mod, 'CT_ENTER_VEHICLE', None)
        log('Vehicle.pickUse called id=%r visible=%r onVeh=%r checkStatus=%r motors=%r passengers=%r' % (
            veh_self.id,
            getattr(owner, 'isModelVisible', None),
            owner.isOnVehicle() if owner and hasattr(owner, 'isOnVehicle') else None,
            owner.checkStatus(ct) if owner and ct is not None else None,
            getattr(getattr(veh_self, 'model', None), 'motors', None),
            getattr(veh_self, 'vPassengers', None)))
        return orig_pick_use(veh_self, *args, **kwargs)

    veh_cls.pickUse = diag_pick_use

    original_enter = getattr(commands, '_hypOriginal_vApplyEnter', commands.vApplyEnter)
    commands._hypOriginal_vApplyEnter = original_enter

    def v_apply_enter(vEntId, specifySeat=False, seatNum=0, p=None):
        if not gameglobal.isOffline:
            return original_enter(vEntId, specifySeat, seatNum, p)
        try:
            owner = BigWorld.player()
            veh = BigWorld.entity(vEntId)
            if owner is None or veh is None or not veh.inWorld:
                return False
            vdata = vd.get(getattr(veh, 'vTypeId', 0), {})
            max_seats = get_seat_count(vdata)
            passengers = list(getattr(veh, 'vPassengers', ()) or [0] * max_seats)
            if owner.id in passengers:
                return False
            if specifySeat and not 0 <= seatNum < len(passengers):
                return False
            target_seat = seatNum if specifySeat else None
            if target_seat is None or target_seat >= len(passengers) or passengers[target_seat]:
                target_seat = None
                for candidate, occ in enumerate(passengers):
                    if not occ:
                        target_seat = candidate
                        break
            if target_seat is None:
                return False
            old_passengers = list(passengers)
            passengers[target_seat] = owner.id
            veh.vPassengers = passengers
            if target_seat == 0:
                veh.vLastDriverId = owner.id
            if hasattr(veh, 'set_vPassengers'):
                veh.set_vPassengers(old_passengers)
            veh.onEnteredVehicle(owner.id, target_seat)
            log('Local vehicle entered id=%r seat=%r' % (vEntId, target_seat))
            return True
        except Exception:
            log('Local vApplyEnter failed: %s' % traceback.format_exc())
            return False

    commands.vApplyEnter = v_apply_enter

    original_leave = getattr(commands, '_hypOriginal_vApplyLeave', commands.vApplyLeave)
    commands._hypOriginal_vApplyLeave = original_leave

    def v_apply_leave(suggestPos=None, p=None):
        if not gameglobal.isOffline:
            return original_leave(suggestPos, p)
        try:
            owner = BigWorld.player()
            info = getattr(owner, 'clientVehicleInfo', None)
            vEntId = info.get('vEntId', 0)
            from_seat = info.get('vSeatNum', 0)
            veh = BigWorld.entity(vEntId) if vEntId else None
            if veh is None:
                return False
            old_passengers = list(getattr(veh, 'vPassengers', ()) or [])
            passengers = [0 if eid == owner.id else eid for eid in old_passengers]
            veh.vPassengers = passengers
            if hasattr(veh, 'set_vPassengers'):
                veh.set_vPassengers(old_passengers)
            pos = suggestPos if suggestPos is not None else owner.getSeatOffsetPos(from_seat, 2.0)
            veh.onLeaveVehicle(owner.id, from_seat, pos)
            log('Local vehicle left id=%r seat=%r' % (vEntId, from_seat))
            return True
        except Exception:
            log('Local vApplyLeave failed: %s' % traceback.format_exc())
            return False

    commands.vApplyLeave = v_apply_leave

    original_switch = getattr(commands, '_hypOriginal_vApplySwitchSeat', commands.vApplySwitchSeat)
    commands._hypOriginal_vApplySwitchSeat = original_switch

    def v_apply_switch(player_arg, targetSeatNum):
        if not gameglobal.isOffline:
            return original_switch(player_arg, targetSeatNum)
        try:
            owner = BigWorld.player()
            info = getattr(owner, 'clientVehicleInfo', None)
            vEntId = info.get('vEntId', 0)
            from_seat = info.get('vSeatNum', 0)
            veh = BigWorld.entity(vEntId) if vEntId else None
            if veh is None:
                return False
            old_passengers = list(getattr(veh, 'vPassengers', ()) or [])
            if not 0 <= targetSeatNum < len(old_passengers) or old_passengers[targetSeatNum]:
                return False
            passengers = [0 if eid == owner.id else eid for eid in old_passengers]
            passengers[targetSeatNum] = owner.id
            veh.vPassengers = passengers
            if targetSeatNum == 0:
                veh.vLastDriverId = owner.id
            if hasattr(veh, 'set_vPassengers'):
                veh.set_vPassengers(old_passengers)
            veh.onSwitchVehicleSeat(owner.id, from_seat, targetSeatNum)
            log('Local vehicle switched seat=%r->%r' % (from_seat, targetSeatNum))
            return True
        except Exception:
            log('Local vApplySwitchSeat failed: %s' % traceback.format_exc())
            return False

    commands.vApplySwitchSeat = v_apply_switch

    for noop_name in ('updateVehicleDir', 'updateVehicleMovement'):
        orig = getattr(commands, '_hypOriginal_' + noop_name, getattr(commands, noop_name))
        setattr(commands, '_hypOriginal_' + noop_name, orig)

        def make_noop(original):
            def noop(*args, **kwargs):
                if not gameglobal.isOffline:
                    return original(*args, **kwargs)
                return None
            return noop

        setattr(commands, noop_name, make_noop(orig))

    def refresh_vehicles():
        if state.get('vehicle_generation') != generation:
            return
        try:
            owner = BigWorld.player()
            if owner is not None and owner.inWorld:
                origin = owner.position
                for eid in list(vehicles):
                    ent = BigWorld.entities.get(eid)
                    if ent is None:
                        vehicles.pop(eid, None)
                        continue
                    if not ent.inWorld:
                        created = vehicles[eid].setdefault('created_at', time.time())
                        if time.time() - created > 30 and not any(ent.vPassengers):
                            BigWorld.destroyEntity(eid)
                            vehicles.pop(eid, None)
                            log('Local vehicle unloaded after model timeout id=%r' % eid)
                        continue
                    dist_sq = sum((a - b) ** 2 for a, b in zip(ent.position, origin))
                    if dist_sq > 160000 and not any(getattr(ent, 'vPassengers', ()) or ()):
                        BigWorld.destroyEntity(eid)
                        vehicles.pop(eid, None)
                if len(vehicles) < state['vehicle_limit'] and not owner.inParachuting():
                    yaw = BigWorld.dcursor().yaw
                    rng = random.Random(int(time.time() * 10) + len(vehicles))
                    offsets = [(18.0, 0.0)] if not vehicles else []
                    while len(offsets) < state['vehicle_limit'] - len(vehicles):
                        offsets.append((rng.uniform(30.0, 180.0), rng.uniform(-math.pi, math.pi)))
                    for dist, ang in offsets:
                        if len(vehicles) >= state['vehicle_limit']:
                            break
                        x = origin.x + math.sin(yaw + ang) * dist
                        z = origin.z + math.cos(yaw + ang) * dist
                        hit = BigWorld.collide(owner.spaceID, (x, 1000.0, z),
                                               (x, -1000.0, z))
                        if hit is None:
                            continue
                        pos = (x, hit[0].y + 0.05, z)
                        if any(sum((a - b) ** 2 for a, b in zip(pos, BigWorld.entities.get(eid).position)) < 144
                               for eid in vehicles if BigWorld.entities.get(eid) is not None):
                            continue
                        vid = land_types[rng.randrange(len(land_types))]
                        spawn_vehicle(vid, pos, rng.uniform(0, math.pi * 2))
                        break
        except Exception:
            log('Local vehicle refresh failed: %s' % traceback.format_exc())
        BigWorld.callback(8.0, refresh_vehicles)

    BigWorld.callback(0.3, refresh_vehicles)

    # Keep PressKeyF F_VEHICLE marker synced with nearby vehicles.
    ui_pick_const = getattr(gameglobal.rds.ui.pressKeyF.addFakeMarker.im_func.func_globals.get('uiPickConst'), 'F_VEHICLE', None)
    if ui_pick_const is None:
        pick_mod = find_module('guis.uipickconst')
        ui_pick_const = getattr(pick_mod, 'F_VEHICLE', None)
    marked_vehicles = state.setdefault('marked_vehicles', set())

    def update_vehicle_markers():
        if state.get('vehicle_generation') != generation:
            return
        if state.get('lobby_active'):
            BigWorld.callback(1, update_vehicle_markers)
            return
        try:
            owner = BigWorld.player()
            pkf = getattr(gameglobal.rds.ui, 'pressKeyF', None)
            if owner is not None and owner.inWorld and pkf is not None and ui_pick_const is not None:
                on_vehicle = owner.isOnVehicle()
                want = set()
                if not on_vehicle:
                    origin = owner.position
                    for eid in list(vehicles):
                        ent = BigWorld.entities.get(eid)
                        if ent is None or not ent.inWorld:
                            continue
                        dist_sq = sum((a - b) ** 2 for a, b in zip(ent.position, origin))
                        if dist_sq <= 20.25:
                            want.add(ent)
                current_marked = pkf.markerEntities.get(ui_pick_const, set())
                removed = [ent for ent in marked_vehicles if ent not in want]
                added = [ent for ent in want if ent not in current_marked]
                if removed:
                    pkf.removeMarker(ui_pick_const, removed)
                    for ent in removed:
                        marked_vehicles.discard(ent)
                if added:
                    pkf.addMarker(ui_pick_const, added)
                    for ent in added:
                        marked_vehicles.add(ent)
                    log('Local vehicle F-prompt shown count=%r pickUI=%r' % (
                        len(added), added[0].getPickUIInfo() if hasattr(added[0], 'getPickUIInfo') else None))
        except Exception:
            log('Local vehicle marker update failed: %s' % traceback.format_exc())
        BigWorld.callback(0.25, update_vehicle_markers)

    BigWorld.callback(0.25, update_vehicle_markers)

    # Bind F7 to summon a nearby vehicle 8m ahead of the player.
    # install_parachute has installed this generation's key handler already.
    orig_key = player.handleKeyEvent.im_func
    type(player)._hypOriginalVehicleHandleKeyEvent = orig_key
    base_key_fn = getattr(type(player), '_hypOriginalWorldHandleKeyEvent', orig_key)
    key_mod = base_key_fn.func_globals.get('keys')
    f7_key = getattr(key_mod, 'KEY_F7', None)
    if f7_key is not None:
        def handle_vehicle_key(owner, is_down, key, mods):
            if gameglobal.isOffline and key == f7_key and is_down:
                try:
                    yaw = BigWorld.dcursor().yaw
                    x = owner.position.x + math.sin(yaw) * 8.0
                    z = owner.position.z + math.cos(yaw) * 8.0
                    hit = BigWorld.collide(owner.spaceID, (x, 1000.0, z), (x, -1000.0, z))
                    if hit is not None:
                        for eid in list(vehicles):
                            entity = BigWorld.entities.get(eid)
                            if entity is not None and not any(entity.vPassengers):
                                BigWorld.destroyEntity(eid)
                                vehicles.pop(eid, None)
                        vid = land_types[int(time.time()) % len(land_types)]
                        spawn_vehicle(vid, (x, hit[0].y + 0.05, z), yaw + math.pi * 0.5)
                except Exception:
                    log('Local F7 vehicle spawn failed: %s' % traceback.format_exc())
                return True
            return orig_key(owner, is_down, key, mods)
        type(player).handleKeyEvent = handle_vehicle_key
        log('Local vehicle summon hotkey ready: F7')


def install(player, state, log):
    install_parachute(player, state, log)
    install_loot(player, state, log)
    install_vehicles(player, state, log)
    lm = gameglobal.rds.ui.littleMap
    rect = getattr(lm, 'mapRect', None)
    if rect and len(rect) == 4 and rect[2] > 0 and rect[3] > 0:
        state['battle_map_rect'] = tuple(rect)
    flight_scope = {}
    execfile('I:/hyxd/hyp/_local_server/offline_flight.py', flight_scope)
    flight_scope['install'](player, state, log)
    if state.get('world_diagnostic_version') == 11:
        return
    module = next((module for name, module in sys.modules.items()
                   if name.lower().endswith('helpers.droppeditemmanager')), None)
    if module is not None:
        records = module.IHRIDD.data
        log('Original loot heap count=%r sample=%r' % (len(records), list(records.items())[:2]))
        log('Original dropped item APIs=%r' % [name for name in dir(module.FakeEntity)
            if any(word in name.lower() for word in ('entity', 'destroy', 'remove'))])
    types = player.refreshAvatarStatus.im_func.func_globals['gametypes']
    log('Original parachute statuses=%r' % {name: getattr(types, name) for name in dir(types)
        if name.startswith('AVATAR_STATUS_') and ('PARA' in name or 'COMBAT' in name)})
    log('Original inventory callbacks=%r' % [name for name in dir(player)
        if any(word in name.lower() for word in ('invupdated', 'equipupdated', 'equipchange', 'invchange', 'ownitem', 'invand', 'equipand'))])
    log('LittleMap diag widget=%r canShow=%r spaceNo=%r teamEntIds=%r teamInfo=%r playerIcon=%r mapRect=%r' % (
        lm.widget, lm.canShow(), getattr(player, 'spaceNo', None),
        getattr(player, 'teamEntIds', None), getattr(player, 'teamInfo', None),
        getattr(lm, 'playerIcon', None), getattr(lm, 'mapRect', None)))
    log('Player energy/hp attrs=%r' % [k for k in dir(player) if any(w in k.lower() for w in ('ep', 'ener', 'power', 'hp'))])
    try:
        ui_utils = module.DroppedItem.getPickUIInfo.im_func.func_globals['uiUtils']
        veh_mod = find_module('vehicle')
        vd = getattr(veh_mod, 'VD', None)
        if vd is not None:
            log('Vehicle defs=%r' % [(vid, sorted(vdata.keys())[:15], vdata.get('vehicleType'), vdata.get('vehicleName'), vdata.get('maxHp'), vdata.get('maxFuel'))
                                     for vid, vdata in sorted(vd.data.items())[:15]])
        item_defs_all = player.clientEquipsAndInv.loadAmmo.im_func.func_globals['ID'].data
        guns = [(iid, ui_utils.getItemNameById(iid), d.get('bulletType'), d.get('slotId'))
                for iid, d in sorted(item_defs_all.items()) if 10000 <= iid < 20000]
        ammos = [(iid, ui_utils.getItemNameById(iid))
                 for iid, d in sorted(item_defs_all.items()) if 30000 <= iid < 40000]
        log('All guns (%d)=%r' % (len(guns), guns))
        log('All ammos (%d)=%r' % (len(ammos), ammos))
        veh_diag = [(eid, getattr(BigWorld.entities.get(eid), 'vTypeId', None),
                     BigWorld.entities.get(eid).isVehicleLoadComplete() if BigWorld.entities.get(eid) else None,
                     getattr(BigWorld.entities.get(eid), 'brokenState', None),
                     getattr(getattr(BigWorld.entities.get(eid), 'model', None), 'attachments', None))
                    for eid in list(state.get('local_vehicles', {}))[:4]]
        log('Local vehicle models=%r' % veh_diag)
        pkf = gameglobal.rds.ui.pressKeyF
        near_veh = next(iter(state.get('marked_vehicles', ())), None)
        log('PressKeyF diag target=%r markers=%r markerEnts=%r veh=%r keyFuncs=%r pickUI=%r motors=%r' % (
            pkf.getPickTargetEnt() if hasattr(pkf, 'getPickTargetEnt') else None,
            getattr(pkf, 'markers', None),
            {k: [getattr(e, 'id', e) for e in v] for k, v in getattr(pkf, 'markerEntities', {}).items()},
            getattr(near_veh, 'id', None),
            near_veh.getKeyFuncInfos() if near_veh and hasattr(near_veh, 'getKeyFuncInfos') else None,
            near_veh.getPickUIInfo() if near_veh and hasattr(near_veh, 'getPickUIInfo') else None,
            getattr(getattr(near_veh, 'model', None), 'motors', None)))
        if vd is not None:
            log('VD detail=%r' % [
                (vid, vdata.get('vehicleType'), vdata.get('modelName'), vdata.get('vehicleModel'),
                 {k: v for k, v in vdata.items() if 'hp' in k.lower() or 'fuel' in k.lower() or 'smoke' in k.lower() or 'broken' in k.lower() or 'type' in k.lower() or 'name' in k.lower()})
                for vid, vdata in sorted(vd.data.items())
            ])
    except Exception:
        log('Catalog diag failed: %s' % traceback.format_exc())
    state['world_diagnostic_version'] = 11
