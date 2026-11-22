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
    if owner.inParachuting() or getattr(owner, 'climbCode', '') or state.get('drop_pending'):
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
    # Native weapon and ammunition IDs already used by the offline loadout.
    choices = ((10203, 1), (10103, 1), (10501, 1),
               (30102, 30), (30103, 10), (30105, 30), (70101, 1), (70111, 1))

    def spawn_record(item_uuid, entry, yaw=0):
        source = entry.get('item')
        props = {'uuid': item_uuid, 'itemId': entry['item_id'],
                 'cwrap': entry['count'], 'heapId': 0,
                 'slots': copy.deepcopy(source.slots) if source is not None else {}}
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
                if not inv.checkInsertToInvByItem(old):
                    return False
                old, extra = inv.removeItemInEquipment(slot, subslot, False, False)
                inv.insertItemToInv(old)
                for extra_item in extra.values():
                    inv.insertItemToInv(extra_item)
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
                 'pickItemToEquipmentFromGround', 'pickItemToEquipmentFromItemHeap'):
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
        try:
            owner = BigWorld.player()
            if owner is None or not owner.inWorld:
                return
            position, now = owner.position, time.time()
            nearby = sorted(((sum((a - b) ** 2 for a, b in zip(data['position'], position)), heap_id, data)
                             for heap_id, data in heaps.items()), key=lambda row: row[0])[:24]
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
                    if active >= 192:
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
        BigWorld.callback(2, update)

    BigWorld.callback(0.2, update)


def install(player, state, log):
    install_parachute(player, state, log)
    install_loot(player, state, log)
    if state.get('world_diagnostic_version') == 1:
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
    state['world_diagnostic_version'] = 1
