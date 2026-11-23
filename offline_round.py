"""Finite local solo rounds, native safe zone and original result screen."""
import copy
import math
import random
import time
import traceback
import uuid

import BigWorld
import gameglobal


# Radius as a fraction of the map's shorter side, wait, shrink time and damage.
ZONE_PHASES = ((0.32, 160, 90, 100), (0.20, 75, 70, 200),
               (0.11, 55, 55, 300), (0.055, 40, 45, 450),
               (0.023, 30, 35, 650), (0.007, 20, 25, 900), (0, 10, 30, 1200))


def circle_at(stage, elapsed):
    outer, inner, wait, shrink = stage[:4]
    fraction = max(0.0, min(1.0, (elapsed - wait) / float(shrink)))
    return outer + (inner - outer) * fraction


def plan_circles(rect, loot_grid, rng):
    left, top, width, depth = rect
    center = (left + width / 2.0, top - depth / 2.0)
    outer = math.hypot(width, depth) / 2.0
    scale = min(width, depth)
    # Use actual settlement coordinates to keep the final zones on land.
    land = [(row['position'][0], row['position'][2])
            for _, rows in sorted(loot_grid.items()) for _, row in rows]
    stages = []
    for fraction, wait, shrink, damage in ZONE_PHASES:
        inner = fraction * scale
        # Limit center travel so reaching the smaller circle stays practical.
        shift = min((outer - inner) * 0.55, scale * 0.17)
        candidates = [point for point in land
                      if (point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2 <= shift ** 2
                      and left + inner * 0.65 <= point[0] <= left + width - inner * 0.65
                      and top - depth + inner * 0.65 <= point[1] <= top - inner * 0.65]
        target = rng.choice(candidates) if candidates and inner else center
        stages.append((outer, inner, wait, shrink, damage, center, target))
        outer, center = inner, target
    return stages


def center_at(stage, elapsed):
    fraction = max(0.0, min(1.0, (elapsed - stage[2]) / float(stage[3])))
    return tuple(a + (b - a) * fraction for a, b in zip(stage[5], stage[6]))


def outside(position, center, radius):
    return (position[0] - center[0]) ** 2 + (position[2] - center[1]) ** 2 > radius ** 2


def install(player, state, log):
    state['round_generation'] = state.get('round_generation', 0) + 1
    generation = state['round_generation']
    native = player.clientEquipsAndInv.loadAmmo.im_func.func_globals
    constants, Item = native['combatconst'], native['Item']
    clock = player.syncSafeAreaInfo.im_func.func_globals['utils'].getTrueNow

    def new_circles():
        return plan_circles(state.get('battle_map_rect', (-4000, 4000, 8000, 8000)),
                            state.get('loot_spatial_grid', {}), random)

    def sync_zone(current, stage, elapsed):
        event = (current['stage'], elapsed >= stage[2])
        if current['zone_event'] == event:
            return
        center, next_center = stage[5:7]
        if event[1]:
            began = clock() - (elapsed - stage[2])
            player.syncSafeAreaShrinkInfo(center, stage[0], next_center, stage[1],
                (stage[0] - stage[1]) / float(stage[3]), began, began + stage[3], event[0] + 1)
        else:
            player.syncSafeAreaInfo(center, stage[0], next_center, stage[1],
                clock() + stage[2] - elapsed, event[0] + 1)
        current['zone_event'] = event
        log('Local round circle stage=%r shrinking=%r radius=%r next=%r center=%r nextCenter=%r' % (
            event[0] + 1, event[1], circle_at(stage, elapsed), stage[1], center, next_center))

    previous_round = state.get('local_round')
    if previous_round and previous_round['phase'] in ('deploying', 'active') and 'circles' not in previous_round:
        previous_round.update(circles=new_circles(), stage=0, stage_started=time.time(), zone_event=None)
    # The offline PlayerAvatar omits lobby-only properties used by the shared
    # match result widget. Supply its local account fields without network RPCs.
    if not hasattr(player, 'lv'):
        player.lv = 1
    if not hasattr(player, 'exp'):
        player.exp = 0
    if not hasattr(player, 'expLimitOfDaily'):
        player.expLimitOfDaily = 0
    if not hasattr(type(player), 'hideAllActivityUI'):
        def hide_activity(owner):
            for name in ('newPlayerReward', 'activityList'):
                proxy = getattr(gameglobal.rds.ui, name, None)
                if proxy is not None:
                    proxy.hide()
        type(player).hideAllActivityUI = hide_activity
    circle = getattr(gameglobal.rds.ui.gasStatusBar, 'gasCircle', None)
    if circle is not None:
        circle_cls = circle.__class__
        original_fetch = getattr(circle_cls, '_hypLocalOriginalFetch', circle_cls.fetchCallback)
        circle_cls._hypLocalOriginalFetch = original_fetch

        def fetch_circle(effect, success):
            # Result/restart may reset the effect while its resource is loading.
            if gameglobal.isOffline and effect.bCEffect is None:
                return
            return original_fetch(effect, success)

        circle_cls.fetchCallback = fetch_circle
    result_proxy = gameglobal.rds.ui.matchResult
    if hasattr(result_proxy, 'setExpInfo'):
        result_cls = result_proxy.__class__
        original_exp = getattr(result_cls, '_hypLocalOriginalExp', result_cls.setExpInfo)
        result_cls._hypLocalOriginalExp = original_exp

        def local_exp(proxy, init=1):
            current = state.get('local_round')
            if (gameglobal.isOffline and current
                    and current.get('result') is proxy.resultInfo):
                if proxy.widget is not None:
                    proxy.widget.expMc.visible = False
                return
            return original_exp(proxy, init)

        result_cls.setExpInfo = local_exp

    def population():
        current = state.get('local_round')
        if current:
            remaining = max(0, current['total'] - current['eliminated'])
            player.onSyncAliveAvatarCnt(remaining + int(player.hp > 0 and not player.isDead()))
            return remaining
        return 0

    def finish(won, reason):
        current = state.get('local_round')
        if not current or current['phase'] not in ('deploying', 'active'):
            return False
        current.update(phase='finished', won=bool(won), reason=reason, ended=time.time())
        stats = state.setdefault('local_profile_stats', {'played': 0, 'wins': 0, 'kills': 0, 'deaths': 0})
        stats['played'] += 1
        stats['wins'] += int(won)
        stats['kills'] += current['kills']
        stats['deaths'] += int(player.isDead())
        state['ai_enabled'] = False
        state.pop('player_respawn_at', None)
        remaining = population()
        result = {'tStart': current['game_started'],
                  'tEnd': max(clock(), current['game_started'] + 1),
                  'member': {player.gbId: {'killSum': current['kills'], 'assistSum': 0,
                      'dmgSum': current['damage'], 'realGender': player.physique.sex}},
                  'rank': 1 if won else remaining + 1, 'totalSideCnt': current['total'] + 1,
                  'restCnt': remaining, 'mode': 0, 'deathReason': 0 if won else 1,
                  'addExpList': [], 'addBindCashList': []}
        current['result'] = result
        current['result_ui_error'] = None
        try:
            gameglobal.rds.ui.gasStatusBar.reset()
            gameglobal.rds.ui.gasStatusBar.hide()
            # Calling onBattleRoyaleEnd also starts a forced quit countdown.
            # Show the original result directly so F10 can restart locally.
            gameglobal.rds.ui.matchResult.show(result)
        except Exception:
            current['result_ui_error'] = traceback.format_exc()
            log('Local round result UI failed: %s' % traceback.format_exc())
        log('Local round finished id=%r won=%r reason=%r kills=%r remaining=%r result=%r' % (
            current['id'], won, reason, current['kills'], remaining, result))
        return True

    state['finish_local_round'] = finish

    def enemy_defeated(entity_id, credited):
        current = state.get('local_round')
        info = state.get('local_targets', {}).get(entity_id, {})
        if not current or current['phase'] != 'active' or info.get('round_id') != current['id']:
            return
        current['eliminated'] += 1
        if credited:
            current['kills'] += 1
        if population() == 0:
            finish(True, 'last_survivor')

    state['on_local_enemy_defeated'] = enemy_defeated

    def clear_enemies():
        state.pop('target_spawn_pending', None)
        for entity_id in list(state.get('local_targets', {})):
            if BigWorld.entities.get(entity_id) is not None:
                BigWorld.destroyEntity(entity_id)
        state['local_targets'] = {}

    def refill_loadout():
        # Each round gets the same saved equipment, with bounded ammunition.
        # Copying avoids inflating the saved loadout on repeated restarts.
        state.setdefault('round_loadout', copy.deepcopy(player.clientEquipsAndInv))
        player.clientEquipsAndInv = copy.deepcopy(state['round_loadout'])
        inv = player.clientEquipsAndInv
        for slot in list(constants.COMBINED_EQUIPMENT_ID_PROP_MAP) + list(constants.SINGLE_EQUIPMENT_ID_PROP_MAP):
            state['refresh_local_inventory'](player, slot)
        supplied = set()
        for gun in player.equipWeapons.values():
            if gun is None or not getattr(gun, 'isGun', False):
                continue
            if inv.getItemInEquipment(gun.slotId, constants.EQUIPMENT_SUBSLOT_BODY) is None:
                continue
            capacity = inv.calcAmmoCapacity(gun.slotId)
            ammo = inv.getItemInEquipment(gun.slotId, constants.EQUIPMENT_SUBSLOT_AMMO)
            if ammo is None:
                inv.insertItemToEquipment(Item(int(uuid.uuid4().hex[:7], 16), gun.bulletType,
                    capacity, {}), gun.slotId, constants.EQUIPMENT_SUBSLOT_AMMO)
            elif ammo.cwrap < capacity:
                inv.equipments[gun.slotId].addAmmo(capacity - ammo.cwrap)
            if gun.bulletType not in supplied:
                reserve = inv.itemListContainer.countTotalWrapById(gun.bulletType)
                if reserve < capacity * 3:
                    inv.itemListContainer.insertItem(Item(int(uuid.uuid4().hex[:7], 16),
                        gun.bulletType, capacity * 3 - reserve, {}))
                supplied.add(gun.bulletType)
            player.onLoadAmmo(capacity, gun.slotId)
        player.updateEquipmentsAndInvData([], inv.checkSum)
        player.refreshCurrentAmmo()

    def start(transport=True, total=8):
        if (not player.inWorld or player.inParachuting() or player.isOnPlane()
                or player.isOnVehicle() or state.get('local_flight')):
            return False
        # Temporarily leave the old round so native practice recovery is legal.
        previous = state.pop('local_round', None)
        if not state['respawn_local_player']():
            if previous:
                state['local_round'] = previous
            return False
        gameglobal.rds.ui.matchResult.hide()
        gameglobal.rds.ui.gasStatusBar.reset()
        clear_enemies()
        refill_loadout()
        old_power = getattr(player, 'power', 0)
        if hasattr(player, 'power'):
            player.power = 0
            player.set_power(old_power)
        state['round_serial'] = state.get('round_serial', 0) + 1
        current = {'id': state['round_serial'], 'phase': 'deploying' if transport else 'active',
                   'total': max(1, int(total)), 'spawned': 0, 'eliminated': 0,
                   'kills': 0, 'damage': 0, 'game_started': clock(),
                   'started': time.time(), 'stage': 0, 'stage_started': time.time(),
                   'circles': new_circles(), 'zone_event': None,
                   'last_damage': time.time(), 'last_population': None}
        state['local_round'] = current
        state['ai_enabled'] = True
        state.pop('player_respawn_at', None)
        population()
        if transport and not state['start_transport_flight']():
            current['phase'] = 'active'
            log('Local round transport unavailable; starting at ground checkpoint')
        flight = state.get('local_flight')
        if flight is not None:
            first = current['circles'][0]
            # Give the last possible drop time enough runway to parachute and travel.
            wait = max(first[2], int(flight.get('route', {}).get('duration', 0) * 0.9 + 90))
            current['circles'][0] = first[:2] + (wait,) + first[3:]
        log('Local round started id=%r total=%r phase=%r' % (
            current['id'], current['total'] + 1, current['phase']))
        return True

    state['start_local_round'] = start

    def update(schedule=True):
        if state.get('round_generation') != generation:
            return
        if state.get('lobby_active'):
            if schedule:
                BigWorld.callback(0.5, update)
            return
        try:
            current = state.get('local_round')
            now = time.time()
            if current and current['phase'] in ('deploying', 'active'):
                if player.hp <= 0 or player.isDead():
                    finish(False, 'death')
                else:
                    if current['phase'] == 'deploying' and not state.get('local_flight') and not player.inParachuting() and not player.isOnPlane():
                        current['phase'] = 'active'
                        state['battle_checkpoint'] = tuple(player.position)
                        log('Local round landed id=%r position=%r' % (current['id'], tuple(player.position)))
                    stage_index = current['stage']
                    circles = current['circles']
                    stage = circles[stage_index]
                    elapsed = now - current['stage_started']
                    while elapsed >= stage[2] + stage[3] and stage_index + 1 < len(circles):
                        current['stage'] += 1
                        current['stage_started'] += stage[2] + stage[3]
                        stage_index = current['stage']
                        stage = circles[stage_index]
                        elapsed = now - current['stage_started']
                    radius = circle_at(stage, elapsed)
                    current.update(radius=radius, center=center_at(stage, elapsed), next_center=stage[6])
                    # Boarding resets native gas visuals. Wait until the aircraft
                    # is ready so the briefing ring is visible throughout flight.
                    flight = state.get('local_flight')
                    if not flight or flight.get('phase') != 'loading':
                        sync_zone(current, stage, elapsed)
                    if current['phase'] == 'deploying':
                        return
                    # Elapsed ticks are capped so a paused client never receives a
                    # lethal backlog of damage when the window resumes.
                    if now - current['last_damage'] >= 1:
                        current['last_damage'] = now
                        if outside(tuple(player.position), current['center'], radius):
                            old_hp = player.hp
                            player.hp = max(0, old_hp - stage[4])
                            player.set_hp(old_hp)
                            if player.hp == 0:
                                state['battle_holding'] = player.holdingEquipment
                                old_life = player.lifeCode
                                player.lifeCode = constants.LIFECODE_DEAD
                                player.set_lifeCode(old_life)
                                finish(False, 'safe_zone')
                        for entity_id, info in list(state.get('local_targets', {}).items()):
                            enemy = BigWorld.entities.get(entity_id)
                            if (enemy is not None and enemy.hp > 0
                                    and info.get('round_id') == current['id']
                                    and outside(tuple(enemy.position), current['center'], radius)):
                                old_hp = enemy.hp
                                enemy.hp = max(0, old_hp - stage[4])
                                enemy.set_hp(old_hp)
                                if enemy.hp == 0:
                                    state['defeat_local_enemy'](entity_id, False)
                    count = current['total'] - current['eliminated']
                    if count != current['last_population']:
                        current['last_population'] = count
                        population()
        except Exception:
            if time.time() - state.get('round_last_error', 0) > 5:
                state['round_last_error'] = time.time()
                log('Local round update failed: %s' % traceback.format_exc())
        finally:
            if schedule:
                BigWorld.callback(0.25, update)

    state['tick_local_round'] = update
    previous_key = player.handleKeyEvent.im_func
    keys = player.setVehiclePhysics.im_func.func_globals['keys']
    round_key = getattr(keys, 'KEY_F10', None)

    def handle_key(owner, is_down, key, mods):
        if gameglobal.isOffline and round_key is not None and key == round_key:
            if is_down:
                start()
            return True
        return previous_key(owner, is_down, key, mods)

    type(player).handleKeyEvent = handle_key
    BigWorld.callback(0.25, update)
    log('Local solo round ready: F10 start/restart, 9 participants, finite enemies and safe zone')
    if state.pop('round_selfcheck_requested', False):
        scope = {}
        execfile('I:/hyxd/hyp/_local_server/verify_offline_round.py', scope)
        BigWorld.callback(2, lambda: scope['run'](state, log))
