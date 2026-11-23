"""Local interaction adapters for original map entities (Python 2 client)."""
import sys
import gc
import math
import time
import BigWorld
import gameglobal


def install(player, state, log):
    helper_module = sys.modules.get('helpers.jumpClimbHelper')
    helper_cls = getattr(helper_module, 'JumpClimbHelper', None)
    if helper_cls is not None:
        original_info = getattr(helper_cls, '_hypOriginalGetClimbInfo', helper_cls.getClimbInfo)
        original_enter = getattr(helper_cls, '_hypOriginalEnterClimb', helper_cls.enterJumpClimb)
        original_exit = getattr(helper_cls, '_hypOriginalExitClimb', helper_cls.exitJumpClimb)
        original_support = getattr(helper_cls, '_hypOriginalSupport', helper_cls.getSupportInfo)
        original_cross = getattr(helper_cls, '_hypOriginalCross', helper_cls.getCrossType)
        original_angle = getattr(helper_cls, '_hypOriginalAngle', helper_cls.calcNeedAdjustYaw)
        original_sweep = getattr(helper_cls, '_hypOriginalSweep', helper_cls.getSweepHitPos)

        def sweep_hit(helper, *args, **kwargs):
            result = original_sweep(helper, *args, **kwargs)
            if hasattr(helper, '_hypProbeTrace'):
                helper._hypProbeTrace.append(('sweep', args[:3], result))
            return result

        def support_info(helper, *args, **kwargs):
            result = original_support(helper, *args, **kwargs)
            helper._hypProbeTrace.append(('support', result))
            return result

        def cross_type(helper, *args, **kwargs):
            result = original_cross(helper, *args, **kwargs)
            helper._hypProbeTrace.append(('cross', result))
            return result

        def adjust_angle(helper, *args, **kwargs):
            result = original_angle(helper, *args, **kwargs)
            helper._hypProbeTrace.append(('angle', result))
            return result

        def get_climb_info(helper):
            helper.owner.model.yaw = BigWorld.dcursor().yaw
            helper._hypProbeTrace = []
            result = original_info(helper)
            now = time.time()
            if result is not None or now - state.get('last_climb_log', 0) > 1:
                state['last_climb_log'] = now
                log('Climb probe result=%r position=%r bodyYaw=%r cameraYaw=%r' % (result, helper.owner.position, helper.owner.model.yaw, BigWorld.dcursor().yaw))
                log('Climb probe detail=%r' % helper._hypProbeTrace)
            return result

        def enter_climb(helper, *args, **kwargs):
            log('Climb entered args=%r' % (args,))
            owner = helper.owner
            # Hand over before faceToDir updates the native model pose. Leaving
            # Servo attached here can restore the preceding frame's heading.
            servo = state.get('pose_servo')
            if servo is not None and servo in owner.model.motors:
                owner.model.delMotor(servo)
            if owner.model.motors:
                matcher = owner.model.motors[0]
                matcher.matchActionOnly = False
                matcher.matcherCoupled = True
                matcher.enableYaw = False
            result = original_enter(helper, *args, **kwargs)
            # enterJumpClimb/faceToDir aligns the entity filter to the wall.
            # The cursor still points along the approach and must not undo it.
            state['climb_heading'] = owner.yaw
            state['climb_start_position'] = tuple(owner.position)
            transform = state.get('render_transform')
            if transform is not None:
                transform.setRotateY(state['climb_heading'])
                transform.translation = owner.position
            log('Climb handoff v2 heading=%r modelYaw=%r cameraYaw=%r position=%r' % (state['climb_heading'], owner.model.yaw, BigWorld.dcursor().yaw, owner.position))
            return result

        def exit_climb(helper):
            log('Climb exited position=%r model=%r' % (helper.owner.position, helper.owner.model.position))
            start = state.get('climb_start_position')
            heading = state.get('climb_heading')
            if start is not None and heading is not None:
                position = helper.owner.position
                forward = (position.x - start[0]) * math.sin(heading) + (position.z - start[2]) * math.cos(heading)
                log('Climb travel forward=%.3f height=%.3f heading=%r modelYaw=%r' % (forward, position.y - start[1], heading, helper.owner.model.yaw))
            result = original_exit(helper)
            state.pop('climb_heading', None)
            state.pop('climb_start_position', None)
            return result

        helper_cls._hypOriginalGetClimbInfo = original_info
        helper_cls._hypOriginalEnterClimb = original_enter
        helper_cls._hypOriginalExitClimb = original_exit
        helper_cls._hypOriginalSupport = original_support
        helper_cls._hypOriginalCross = original_cross
        helper_cls._hypOriginalAngle = original_angle
        helper_cls._hypOriginalSweep = original_sweep
        helper_cls.getClimbInfo = get_climb_info
        helper_cls.enterJumpClimb = enter_climb
        helper_cls.exitJumpClimb = exit_climb
        helper_cls.getSupportInfo = support_info
        helper_cls.getCrossType = cross_type
        helper_cls.calcNeedAdjustYaw = adjust_angle
        helper_cls.getSweepHitPos = sweep_hit
    doors = []
    for entity in BigWorld.entities.values():
        if hasattr(entity, 'doorId') and hasattr(entity, 'refreshSwitchStatus'):
            doors.append(entity)
    log('Local doors loaded=%r' % len(doors))
    modules = [module for name, module in sys.modules.items()
               if name.lower().endswith('dooronline')]
    for module in modules:
        cls = getattr(module, 'DoorOnline', None)
        if cls is None:
            continue
        original = getattr(cls, '_hypOriginalSwitchDoor', cls.switchDoor)

        def switch_door(door, status, original=original):
            if not gameglobal.isOffline:
                return original(door, status)
            old_status = door.switchStatus
            utils = door.refreshDoorStatus.im_func.func_globals['utils']
            packed_status = utils.spliceDoorStatus(status, door.brokenStatus)
            door.refreshDoorStatus(packed_status)
            manager = state.get('building_manager')
            if manager is not None:
                manager.applyDoorStatusChanged(utils.getChunkId(door.position), {door.doorId: {'status': packed_status}})
            log('Local door id=%r status=%r->%r' % (door.doorId, old_status, status))

        cls._hypOriginalSwitchDoor = original
        cls.switchDoor = switch_door
        log('Installed original door interaction adapter')
    for module in modules:
        cls = getattr(module, 'DoorOnline', None)
        if cls is not None:
            objects = [obj for obj in gc.get_objects() if isinstance(obj, cls)]
            log('Door instances=%r samples=%r' % (len(objects), [(getattr(obj, 'doorId', None), getattr(obj, 'position', None)) for obj in objects[:3]]))
    log('Local interaction modules=%r' % [name for name in sys.modules
        if any(word in name.lower() for word in ('climbhelper', 'dooronline'))])
    for name in ('DoorOnline', 'helpers.DoorOnline'):
        module = sys.modules.get(name)
        log('Door module %s fields=%r' % (name, dir(module)))
        cls = getattr(module, 'DoorOnline', None)
        if cls is not None:
            log('Door class %s module=%r methods=%r' % (name, cls.__module__, dir(cls)))
    log('Climb status physics=%r matcher=%r yaw=%r modelYaw=%r' % (player.physics.isClimbing, getattr(player, 'cm', None), player.yaw, player.model.yaw))
    log('Engine door/model APIs=%r' % [name for name in dir(BigWorld) if any(word in name.lower() for word in ('door', 'model', 'embodiment', 'userdata'))])
    log('Engine entities=%r' % [(entity.id, type(entity).__name__) for entity in BigWorld.entities.values()][:40])
    old_doors = [obj for obj in gc.get_objects() if type(obj).__name__ == 'Door']
    log('Original door instances=%r examples=%r' % (len(old_doors), [(getattr(door, 'position', None), getattr(door, 'model', None)) for door in old_doors[:3]]))
    log('Entity destruction APIs=%r' % [name for name in dir(BigWorld) if 'destroy' in name.lower()])
    manager = player.enterWorld.im_func.func_globals.get('buildingPartsManager')
    manager = manager.instance if manager is not None else None
    if manager is not None and hasattr(manager, 'hasBuildingPartsChunkIds'):
        state['building_manager'] = manager
        if state.get('geometry_probed') != 3:
            scope = {}
            execfile('I:/hyxd/hyp/_local_server/probe_offline_climb.py', scope)
            scope['probe'](player, log)
            state['geometry_probed'] = 3
        if not state.get('window_data_logged'):
            module_globals = manager.createSingleChunkItem.im_func.func_globals
            window_data = module_globals['WINDD'].data
            near_windows = sorted(window_data.items(), key=lambda pair: sum((a-b)**2 for a, b in zip(pair[1]['position'], player.position)))[:3]
            log('Nearby window configuration=%r' % near_windows)
            state['window_data_logged'] = True
        log('Building chunks=%r cached=%r' % (len(manager.hasBuildingPartsChunkIds), len(manager.chunkInfo)))
        manager.updateBuildingParts()
        state['interaction_generation'] = state.get('interaction_generation', 0) + 1
        generation = state['interaction_generation']
        last_chunk = [None]

        def update_local_chunks():
            if state.get('interaction_generation') != generation:
                return
            current_player = BigWorld.player()
            if not state.get('lobby_active') and current_player is not None and current_player.inWorld:
                chunk = manager.isNearByChunk.im_func.func_globals['utils'].getChunkId(current_player.position)
                if last_chunk[0] != chunk:
                    last_chunk[0] = chunk
                    manager.updateBuildingParts()
                    log('Updated local door/window chunks at %r' % (chunk,))
                placeholders = [entity.id for entity in BigWorld.entities.values()
                                if type(entity).__name__ in ('Door', 'Window')]
                for entity_id in placeholders:
                    BigWorld.destroyEntity(entity_id)
                if placeholders:
                    log('Removed %r static door/window placeholders; interactive versions retained' % len(placeholders))
            BigWorld.callback(1.0, update_local_chunks)

        BigWorld.callback(1.0, update_local_chunks)
