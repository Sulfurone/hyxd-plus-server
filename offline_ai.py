"""Bounded local control of original AvatarRobot models, weapons and bullets."""
import math
import random
import sys
import time
import traceback

import BigWorld
import Math
import gameglobal


def damage_after_armor(base_damage, multiplier, armor):
    damage = max(0, int(round(base_damage * multiplier)))
    ratio, duration, protects = armor
    ratio = float(ratio)
    if ratio > 1:
        ratio /= 10000.0
    if duration <= 0:
        ratio = 0
    return int(round(damage * (1 - max(0, min(1, ratio)))))


def install(player, state, log):
    module = next((module for name, module in sys.modules.items()
                   if name.lower().endswith('avatarrobot')), None)
    guns = next((module for name, module in sys.modules.items()
                 if name.lower().endswith('weapon.weapongun')), None)
    if module is None or guns is None:
        requested_generation = getattr(gameglobal, '_hypOfflineGeneration', None)
        if 'ai_wait_since' not in state:
            state['ai_wait_since'] = time.time()
            log('Local AI waiting for native entity and weapon modules')
        if time.time() - state['ai_wait_since'] < 30:
            def retry_install():
                if getattr(gameglobal, '_hypOfflineGeneration', None) == requested_generation:
                    install(player, state, log)
            BigWorld.callback(0.5, retry_install)
        else:
            log('Local AI installation timed out waiting for original client modules')
        return
    state.pop('ai_wait_since', None)
    robot_cls, gun_cls = module.AvatarRobot, guns.RobotNormalGun
    native = player.clientEquipsAndInv.loadAmmo.im_func.func_globals
    constants = native['combatconst']
    state['ai_generation'] = state.get('ai_generation', 0) + 1
    generation = state['ai_generation']
    state.setdefault('ai_enabled', False)
    state.setdefault('local_enemy_count', 2)
    state.setdefault('battle_checkpoint', tuple(player.position))
    state.setdefault('battle_alive_code', player.lifeCode)
    state.setdefault('battle_kills', 0)
    state.setdefault('battle_deaths', 0)

    def local(robot):
        return gameglobal.isOffline and robot.id in state.get('local_targets', {})

    def entry(robot):
        return state.get('local_targets', {}).get(robot.id, {})

    for name in ('bulletCnt', 'bulletMaxCnt', 'bulletInBag'):
        original = getattr(gun_cls, '_hypLocalOriginal_' + name, getattr(gun_cls, name))
        setattr(gun_cls, '_hypLocalOriginal_' + name, original)

        def ammo(weapon, name=name, original=original):
            if not local(weapon.owner):
                return original.fget(weapon)
            data = weapon.getItemData()
            capacity = int(data.get('defaultCapacity', 30))
            if name == 'bulletMaxCnt':
                return capacity
            if name == 'bulletInBag':
                return capacity * 5
            return entry(weapon.owner).setdefault('ammo', capacity)

        setattr(gun_cls, name, property(ammo))

    clientcom = robot_cls.fireBullet.im_func.func_globals['clientcom']
    original_enabled = getattr(clientcom, '_hypOriginalRobotEnabled', clientcom.enableRobot)
    clientcom._hypOriginalRobotEnabled = original_enabled

    def robot_enabled():
        if gameglobal.isOffline:
            return bool(state.get('ai_enabled'))
        return original_enabled()

    clientcom.enableRobot = robot_enabled
    original_suggest = getattr(robot_cls, '_hypOriginalSuggestFace', robot_cls.suggestFaceDir)
    robot_cls._hypOriginalSuggestFace = original_suggest

    def suggest_face(robot, direction):
        if not local(robot):
            return original_suggest(robot, direction)
        info = entry(robot)
        transform = info.get('motion_matrix')
        if transform is not None:
            position = tuple(robot.position)
            transform.setRotateY(direction[1])
            transform.translation = Math.Vector3(*position)

    robot_cls.suggestFaceDir = suggest_face
    original_begin = getattr(robot_cls, '_hypOriginalBeginLocalFire', robot_cls.broadCastBeginAttackWithRayWeapon)
    robot_cls._hypOriginalBeginLocalFire = original_begin

    def begin_fire(robot, timestamp, start, rays, slot):
        if not local(robot):
            return original_begin(robot, timestamp, start, rays, slot)
        info = entry(robot)
        gun = robot.getActivedWeapon()
        info['ammo'] = max(0, gun.bulletCnt - 1)
        info['shots'] = info.get('shots', 0) + 1
        if info['shots'] % 10 == 1:
            log('Local AI native shot id=%r count=%r ammo=%r' % (robot.id, info['shots'], info['ammo']))

    robot_cls.broadCastBeginAttackWithRayWeapon = begin_fire
    avatar_cls = type(player)
    original_awareness = getattr(avatar_cls, '_hypOriginalRobotShotAwareness', avatar_cls.broadcastfirebulletToRobot)
    original_awareness = getattr(original_awareness, 'im_func', original_awareness)
    avatar_cls._hypOriginalRobotShotAwareness = original_awareness

    def shot_awareness(owner, start, direction, timestamp):
        if not gameglobal.isOffline:
            return original_awareness(owner, start, direction, timestamp)
        # Native code sends this awareness message to robot.cell. Local bots
        # already perceive the player through our bounded controller.
        for info in state.get('local_targets', {}).values():
            info['next_fire'] = min(info.get('next_fire', time.time() + 1), time.time() + 0.3)

    avatar_cls.broadcastfirebulletToRobot = shot_awareness

    def respawn():
        current_round = state.get('local_round')
        if current_round and current_round.get('phase') in ('deploying', 'active'):
            return False
        owner = BigWorld.player()
        if owner is None or not owner.inWorld:
            return False
        if owner.isOnVehicle():
            native_commands = owner.reloadAmmoActionEnd.im_func.func_globals['serverCmd']
            native_commands.vApplyLeave(None)
            state['player_respawn_at'] = time.time() + 0.5
            return True
        if owner.isOnPlane() or owner.inParachuting():
            return False
        old_life = owner.lifeCode
        owner.lifeCode = state['battle_alive_code']
        owner.hp = owner.maxHp
        owner.set_lifeCode(old_life)
        owner.set_hp(0)
        owner.tryChangeToNormalMove()
        owner.ap.resetControl()
        owner.physics.teleport(Math.Vector3(*state['battle_checkpoint']))
        preferred = state.get('battle_holding', 0)
        if not preferred or preferred not in owner.equipWeapons:
            preferred = next((slot for slot, weapon in owner.equipWeapons.items()
                if weapon is not None and getattr(weapon, 'isGun', False)
                and owner.clientEquipsAndInv.getItemInEquipment(slot, constants.EQUIPMENT_SUBSLOT_BODY) is not None), 0)
        if preferred:
            owner.switchHoldingEquip(preferred, True, True)
        gameglobal.rds.ui.pressKeyF.show()
        state.pop('player_respawn_at', None)
        state['player_protected_until'] = time.time() + 5
        for info in state.get('local_targets', {}).values():
            info['next_fire'] = time.time() + 5
        log('Local player respawned hp=%r life=%r position=%r' % (
            owner.hp, owner.lifeCode, state['battle_checkpoint']))
        def check_control():
            gun = owner.getActivedWeapon()
            log('Local respawn control weapon=%r canFire=%r action=%r equipState=%r' % (
                type(gun).__name__ if gun is not None else None,
                gun.canPrimaryAttack() if gun is not None else None,
                owner.fashion.getDoingActionType(), type(owner.equipState).__name__))
        BigWorld.callback(1, check_control)
        return True

    state['respawn_local_player'] = respawn
    original_hit = getattr(robot_cls, '_hypOriginalLocalBulletHit', robot_cls.broadCastAttackWithRayWeapon_robot)
    robot_cls._hypOriginalLocalBulletHit = original_hit

    def hit(robot, start, end, direction, hit_id, node, shape, timestamp, distributed):
        if not local(robot):
            return original_hit(robot, start, end, direction, hit_id, node, shape, timestamp, distributed)
        owner = BigWorld.player()
        if not state.get('ai_enabled') or owner is None or hit_id != owner.id or owner.hp <= 0:
            return False
        if time.time() < state.get('player_protected_until', 0) or owner.inParachuting() or owner.isOnPlane():
            return False
        gun = robot.getActivedWeapon()
        if gun is None:
            return False
        data = gun.getItemData()
        hit_globals = original_hit.im_func.func_globals
        raw_part = hit_globals['gametypes'].RIGIDBODY_NODE_NAME_DICT.get(node)
        if raw_part is None:
            log('Local AI hit unknown body node=%r' % node)
            return False
        utils = hit_globals['utils']
        part = utils.getHitId(utils.setHitId(raw_part, True))
        inv = owner.clientEquipsAndInv
        armor_slot = constants.EQUIPMENT_SLOT_ID_HEAD if part in (1, 2) else constants.EQUIPMENT_SLOT_ID_VEST
        armor = inv.getHeadDamageReduce() if part in (1, 2) else inv.getBodyDamageReduce()
        if part not in armor[2]:
            armor = (0, 0, ())
        multiplier = data.get('partDamage', {}).get(part, 1)
        damage = damage_after_armor(data.get('hitDamage', 0), multiplier, armor)
        old_hp = owner.hp
        owner.hp = max(0, old_hp - damage)
        if armor[1] > 0:
            inv.reduceEquipDuration(armor_slot, damage, False)
            owner.updateEquipmentsAndInvData([], inv.checkSum)
        owner.set_hp(old_hp)
        owner.playBeHittedEffect(node, robot.id)
        state['last_player_damage'] = time.time()
        log('Local AI bullet hit attacker=%r part=%r damage=%r hp=%r->%r' % (
            robot.id, part, damage, old_hp, owner.hp))
        if owner.hp == 0:
            state['battle_holding'] = owner.holdingEquipment
            old_life = owner.lifeCode
            owner.lifeCode = constants.LIFECODE_DEAD
            owner.set_lifeCode(old_life)
            state['battle_deaths'] += 1
            current_round = state.get('local_round')
            if current_round and current_round.get('phase') in ('deploying', 'active'):
                state['finish_local_round'](False, 'bullet')
            else:
                state['player_respawn_at'] = time.time() + 8
            log('Local player defeated deaths=%r nativeDead=%r' % (state['battle_deaths'], owner.isDead()))
        return True

    robot_cls.broadCastAttackWithRayWeapon_robot = hit

    def prepare(robot, info):
        if not info.get('ready') or robot.hp <= 0 or info.get('ai_ready'):
            return
        robot.switchHoldingEquip(1, False, True)
        robot.fireTarget = player.id
        robot.isBindedHere = True
        transform = Math.Matrix()
        transform.setRotateY(robot.yaw)
        transform.translation = robot.position
        robot.filter.followTarget = transform
        info['motion_matrix'] = transform
        info['ai_ready'] = True
        info['last_tick'] = time.time()
        info['next_fire'] = time.time() + 3
        info['next_move'] = time.time() + 1
        log('Local AI native controller ready id=%r gun=%r hp=%r' % (
            robot.id, robot.getActivedWeapon().id, robot.hp))

    def update():
        if state.get('ai_generation') != generation:
            return
        try:
            owner = BigWorld.player()
            now = time.time()
            if owner is None or not owner.inWorld:
                BigWorld.callback(1, update)
                return
            respawn_at = state.get('player_respawn_at')
            if respawn_at is not None and now >= respawn_at:
                respawn()
            current_round = state.get('local_round')
            round_active = current_round is None or current_round.get('phase') == 'active'
            active = (state.get('ai_enabled') and round_active and owner.hp > 0
                and not owner.isDead() and not owner.inParachuting()
                and not owner.isOnPlane() and not owner.isOnVehicle())
            targets = state.get('local_targets', {})
            for entity_id, info in list(targets.items()):
                robot = BigWorld.entities.get(entity_id)
                if robot is None or not robot.inWorld or robot.hp <= 0:
                    continue
                prepare(robot, info)
                if not info.get('ai_ready'):
                    continue
                elapsed = max(0, min(0.25, now - info.get('last_tick', now)))
                info['last_tick'] = now
                if not active:
                    robot.fireTarget = 0
                    continue
                robot.fireTarget = owner.id
                origin = robot.position
                delta = owner.position - origin
                distance = math.sqrt(delta.x**2 + delta.z**2)
                if distance > 60 and current_round is None:
                    continue
                aim_from = (origin.x, origin.y + 1.4, origin.z)
                aim_to = (owner.position.x, owner.position.y + 1.2, owner.position.z)
                if now - info.get('last_vision_check', 0) >= 0.35:
                    info['visible'] = BigWorld.collide(owner.spaceID, aim_from, aim_to) is None
                    info['last_vision_check'] = now
                visible = info.get('visible', False)
                heading = math.atan2(delta.x, delta.z)
                matrix = info['motion_matrix']
                matrix.setRotateY(heading)
                matrix.translation = origin
                if (visible or current_round is not None) and distance > 12 and now >= info.get('next_move', 0):
                    step = min(0.5, 1.5 * elapsed)
                    x = origin.x + math.sin(heading) * step
                    z = origin.z + math.cos(heading) * step
                    ground = BigWorld.collide(owner.spaceID, (x, origin.y + 0.7, z), (x, origin.y - 1, z))
                    wall = BigWorld.collide(owner.spaceID, (origin.x, origin.y + 0.4, origin.z), (x, origin.y + 0.4, z))
                    if ground is not None and wall is None and abs(ground[0].y - origin.y) < 0.45:
                        matrix.translation = Math.Vector3(x, ground[0].y + 0.02, z)
                if not visible or distance > 45 or now < info.get('next_fire', 0):
                    continue
                gun = robot.getActivedWeapon()
                if gun.bulletCnt == 0:
                    if not info.get('reload_until'):
                        info['reload_until'] = now + 2.5
                        robot.playReloadStartAction()
                    elif now >= info['reload_until']:
                        info['ammo'] = gun.bulletMaxCnt
                        info.pop('reload_until', None)
                        robot.playReloadEndAction()
                        log('Local AI reloaded id=%r ammo=%r' % (robot.id, info['ammo']))
                    continue
                robot.onClientAIFireTimerRobot(owner, max(elapsed, 0.1))
                info['next_fire'] = now + 0.8
            if now - state.get('last_ai_spawn_check', 0) > 2:
                state['last_ai_spawn_check'] = now
                if active and len(targets) < state.get('local_enemy_count', 2):
                    scope = {}
                    execfile('I:/hyxd/hyp/_local_server/offline_targets.py', scope)
                    scope['install'](owner, state, log)
            if active and now - state.get('last_player_damage', 0) > 15 and now - state.get('checkpoint_updated', 0) > 10:
                if owner.getDistanceFromGround() < 2:
                    state['battle_checkpoint'] = tuple(owner.position)
                    state['checkpoint_updated'] = now
        except Exception:
            if time.time() - state.get('ai_last_error', 0) > 3:
                state['ai_last_error'] = time.time()
                log('Local AI update failed: %s' % traceback.format_exc())
        BigWorld.callback(0.15, update)

    previous_key = player.handleKeyEvent.im_func
    keys = player.setVehiclePhysics.im_func.func_globals['keys']
    pause_key = getattr(keys, 'KEY_F5', None)
    respawn_key = getattr(keys, 'KEY_F6', None)
    def handle_key(owner, is_down, key, mods):
        if gameglobal.isOffline and key in (pause_key, respawn_key):
            if is_down:
                if key == pause_key:
                    state['ai_enabled'] = not state.get('ai_enabled')
                    log('Local AI enabled=%r' % state['ai_enabled'])
                else:
                    if not state.get('local_round'):
                        respawn()
            return True
        return previous_key(owner, is_down, key, mods)
    type(player).handleKeyEvent = handle_key
    BigWorld.callback(0.3, update)
    log('Local active AI ready: F5 toggle combat, F6 recover player')
    if state.pop('battle_selfcheck_requested', False):
        def selfcheck():
            scope = {}
            execfile('I:/hyxd/hyp/_local_server/verify_offline_battle.py', scope)
            scope['run'](state, log)
        BigWorld.callback(2, selfcheck)
