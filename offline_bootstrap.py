"""Local startup bridge, executed by the installed Python 2 game client."""
import os
import sys
import time
import traceback

import BigWorld
import Math
import game
import gameglobal

SOURCE = 'I:/hyxd/hyp/_local_server/offline_bootstrap.py'
LOG = 'I:/hyxd/hyp/_local_server/logs/offline-bootstrap.log'
SPAWN = (61.0, 60.0, 108.0)

gameglobal._hypOfflineGeneration = getattr(gameglobal, '_hypOfflineGeneration', 0) + 1
GENERATION = gameglobal._hypOfflineGeneration
gameglobal._hypOfflineMotionGeneration = getattr(gameglobal, '_hypOfflineMotionGeneration', 0) + 1
STATE = getattr(gameglobal, '_hypLocalOffline', {})
gameglobal._hypLocalOffline = STATE
_last_position = None
_last_facing = None
_last_facing_time = 0


def log(message):
    with open(LOG, 'a') as stream:
        stream.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), message))


def stage(message):
    if STATE.get('stage') != message:
        STATE['stage'] = message
        log(message)


def current():
    return GENERATION == gameglobal._hypOfflineGeneration


def retry(delay=1.0):
    BigWorld.callback(delay, run)


def bind_model(player):
    climbing = getattr(player, 'climbCode', '') or (player.physics is not None and player.physics.isClimbing)
    if climbing:
        servo = STATE.get('pose_servo')
        if servo is not None and servo in player.model.motors:
            player.model.delMotor(servo)
        heading = STATE.get('climb_heading', player.yaw)
        transform = STATE.get('render_transform')
        if transform is not None:
            transform.setRotateY(heading)
            transform.translation = player.position
        # Native actionPromoteCallback changes matcherCoupled while animation
        # root motion runs. Do not overwrite that decision each frame.
        player.model.yaw = heading
        return
    if 'render_transform' not in STATE:
        STATE['render_transform'] = Math.Matrix()
    transform = STATE['render_transform']
    transform.setRotateY(BigWorld.dcursor().yaw)
    transform.translation = player.position
    if player.model.motors and STATE.get('bound_model') is not player.model:
        matcher = player.model.motors[0]
        matcher.target = transform
        # Keep original animation; local entities have no server transform stream.
        matcher.matchActionOnly = True
        matcher.matcherCoupled = False
        matcher.enableYaw = False
        STATE['bound_model'] = player.model
    if STATE.get('pose_servo_model') is not player.model:
        servo = BigWorld.Servo(transform)
        STATE['pose_servo'] = servo
        STATE['pose_servo_model'] = player.model
        log('Created native model pose motor')
    servo = STATE['pose_servo']
    if servo not in player.model.motors:
        player.model.addMotor(servo)
    player.model.position = player.position
    player.model.yaw = BigWorld.dcursor().yaw
    if player.model.motors:
        matcher = player.model.motors[0]
        matcher.enableYaw = False
        matcher.matchActionOnly = True
        matcher.matcherCoupled = False


def run():
    if not current():
        return
    try:
        if not hasattr(gameglobal, '_hypOfflineStderr'):
            gameglobal._hypOfflineStderr = open('I:/hyxd/hyp/_local_server/logs/offline-python-errors.log', 'a', 0)
        sys.stderr = gameglobal._hypOfflineStderr
        if not hasattr(gameglobal.rds.ui, 'uiObj'):
            stage('Waiting for UI initOK')
            retry()
            return
        gameglobal.isOffline = True
        BigWorld.enableOfflineFocus(False)
        player = BigWorld.player()
        if player is None:
            manager = game.lobbyScene.getManagerInstance()
            if manager.lobbyScene is not None:
                manager.lobbyScene.unLoadSpace()
            if 'space' not in STATE:
                STATE['space'] = BigWorld.createSpace()
            space = STATE['space']
            gameglobal.rds.spaceName = 'universes/eg/hukou'
            if 'mapping' not in STATE:
                STATE['mapping'] = BigWorld.addSpaceGeometryMapping(space, None, 'universes/eg/hukou')
                gameglobal.rds.clientSpaceMapping = STATE['mapping']
                log('Loaded original hukou map into space %r' % space)
            info = game.getOfflineAvatarInfo()
            info.update(hp=100, maxHp=100, avatarStatus=5)
            player_id = BigWorld.createEntity('Avatar', space, 0, Math.Vector3(*SPAWN), Math.Vector3(0, 0, 0), info)
            BigWorld.player(BigWorld.entities.get(player_id))
            player = BigWorld.player()
            log('Created original Avatar id=%r' % player_id)
        if not hasattr(player, 'clientEquipsAndInv'):
            player.onBecomePlayer()
        if player.physics is None:
            player.enterWorld()
        BigWorld.camera().spaceID = player.spaceID
        BigWorld.darkWorld(False)
        BigWorld.setTerrainEnable(True)
        BigWorld.setFloraEnable(True)
        BigWorld.worldDrawEnabled(True)
        sources = getattr(player.model, 'sources', ()) if player.model else ()
        if not sources or sources == ('effect/dummy/effectdummy.model',):
            if time.time() - STATE.get('body_retry_at', 0) > 10:
                player.modelServer.bodyUpdateStatus = 0
                player.modelServer.bodyUpdate()
                STATE['body_retry_at'] = time.time()
            stage('Waiting for original body model')
            retry()
            return
        player.maxHp = 100
        player.hp = 100
        types = player._onLoaded.im_func.func_globals['gametypes']
        if player.avatarStatus != types.AVATAR_STATUS_IN_COMBAT:
            player.avatarStatus = types.AVATAR_STATUS_IN_COMBAT
            player.refreshAvatarStatus(player.avatarStatus)
        if not STATE.get('positioned'):
            hit = BigWorld.collide(player.spaceID, (SPAWN[0], 1000.0, SPAWN[2]), (SPAWN[0], -1000.0, SPAWN[2]))
            if hit is None:
                stage('Waiting for spawn collision geometry')
                retry()
                return
            position = hit[0]
            player.physics.teleport(Math.Vector3(position.x, position.y + 0.2, position.z))
            STATE['positioned'] = True
        bind_model(player)
        gameglobal.rds.ui.loginView.hide()
        gameglobal.rds.ui.littleMap.show()
        gameglobal.rds.ui.playerChat.show()
        if hasattr(player, 'set_maxHp'):
            player.set_maxHp(0)
        if hasattr(player, 'set_hp'):
            player.set_hp(0)
        STATE['ready'] = True
        stage('Offline map and player ready')
        log('Native transform motors=%r' % [name for name in dir(BigWorld) if any(word in name.lower() for word in ('servo', 'motor', 'tracker', 'yaw', 'filter', 'matrix'))])
        log('Combat snapshot holding=%r hp=%r maxHp=%r equipState=%r inScriptControl=%r' % (player.holdingEquipment, player.hp, player.maxHp, player.equipState, player.inScriptControl))
        interaction_scope = {}
        execfile('I:/hyxd/hyp/_local_server/offline_interactions.py', interaction_scope)
        interaction_scope['install'](player, STATE, log)
        combat_scope = {}
        execfile('I:/hyxd/hyp/_local_server/offline_combat.py', combat_scope)
        combat_scope['install'](player, STATE, log)
        weapon = player.getActivedWeapon()
        if weapon and hasattr(weapon, 'waitRealThrow') and weapon.isThrow:
            log('Completing stalled local grenade throw')
            weapon.realThrowGrenade()
            weapon.isHold = False
            weapon.isThrow = False
            weapon.waitRealThrow = False
        cam = BigWorld.camera()
        dc = BigWorld.dcursor()
        ap = getattr(player, 'ap', None)
        ap_info = {k: getattr(ap, k, None) for k in dir(ap) if 'yaw' in k.lower() or 'turn' in k.lower() or 'seek' in k.lower() or 'lock' in k.lower() or 'dir' in k.lower()} if ap else {}
        dc_info = {k: getattr(dc, k, None) for k in dir(dc) if not k.startswith('_') and not callable(getattr(dc, k, None))}
        log('Ready id=%r pos=%r yaw=%r model_yaw=%r cam_dir=%r dc=%r ap=%r' % (
            player.id, player.position, player.yaw, player.model.yaw, getattr(cam, 'direction', None),
            dc_info, ap_info))
    except Exception:
        log(traceback.format_exc())
        retry(2.0)


def monitor_movement():
    global _last_position, _last_facing, _last_facing_time
    if not current():
        return
    try:
        player = BigWorld.player()
        if STATE.get('ready') and player is not None and player.model is not None:
            native_model_yaw = player.model.yaw
            bind_model(player)
            facing = (BigWorld.dcursor().yaw, player.yaw, player.model.yaw)
            if time.time() - _last_facing_time > 0.5 and (not _last_facing or abs(facing[0] - _last_facing[0]) > 0.15):
                log('Facing cameraYaw=%r playerYaw=%r modelYaw=%r nativeModelYaw=%r pitch=%r roll=%r' % (facing[0], facing[1], facing[2], native_model_yaw, player.pitch, player.roll))
                _last_facing = facing
                _last_facing_time = time.time()
            position = tuple(player.position)
            if _last_position is None or sum((a-b)**2 for a, b in zip(position, _last_position)) > 0.0025:
                log('Movement position=%r model=%r camera=%r' % (position, player.model.position, BigWorld.camera().position))
                _last_position = position
    except Exception:
        log(traceback.format_exc())
    BigWorld.callback(0.0, monitor_movement)


def watch_source(previous=os.stat(SOURCE).st_mtime):
    if not current():
        return
    if os.stat(SOURCE).st_mtime != previous:
        execfile(SOURCE, {'__name__': '_hyp_local_offline'})
    else:
        BigWorld.callback(1.0, watch_source)


run()
BigWorld.callback(0.0, monitor_movement)
BigWorld.callback(1.0, watch_source)
