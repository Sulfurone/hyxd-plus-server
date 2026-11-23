"""Local transport flight using the original Plane and parachute states."""
import math
import time
import traceback

import BigWorld
import Math
import gameglobal


DEFAULT_MAP_RECT = (-4000, 4000, 8000, 8000)
TRANSPORT_SPEED = 60.0


def route_at(route, elapsed):
    fraction = max(0.0, min(1.0, elapsed / route['duration']))
    return tuple(a + (b - a) * fraction for a, b in zip(route['source'], route['destination']))


def map_route(rect, yaw, height):
    left, top, width, depth = rect
    x, z = left + width / 2.0, top - depth / 2.0
    dx, dz = math.sin(yaw), math.cos(yaw)
    # Both ends are just inside the boundary so boarding and automatic exit
    # take place in the mapped area.
    inset = min(160.0, width * 0.1, depth * 0.1)
    half_span = min(half / abs(direction) for half, direction in
                    ((width / 2.0 - inset, dx), (depth / 2.0 - inset, dz))
                    if abs(direction) > 1e-8)
    return {'source': (x - dx * half_span, height, z - dz * half_span),
            'destination': (x + dx * half_span, height, z + dz * half_span),
            'duration': 2.0 * half_span / TRANSPORT_SPEED}


class PlaneMapPosition(object):
    def __init__(self, plane, transform):
        self.plane = plane
        self.transform = transform

    @property
    def position(self):
        return self.transform.translation

    def __getattr__(self, name):
        return getattr(self.plane, name)


def install(player, state, log):
    types = player.refreshAvatarStatus.im_func.func_globals['gametypes']
    commands = player.pickUse.im_func.func_globals['serverCmd']
    state['flight_generation'] = state.get('flight_generation', 0) + 1
    generation = state['flight_generation']
    probe_id = state.pop('flight_probe_entity', None)
    if probe_id is not None and BigWorld.entities.get(probe_id) is not None:
        BigWorld.destroyEntity(probe_id)

    ui = getattr(gameglobal.rds, 'ui', None)
    if ui is not None:
        clientcom = ui.littleMap.refreshAirline.im_func.func_globals['clientcom']
        original_get_plane = getattr(clientcom, '_hypOriginalGetPlaneEntity', clientcom.getPlaneEntity)
        clientcom._hypOriginalGetPlaneEntity = original_get_plane

        def get_plane():
            if gameglobal.isOffline:
                flight = state.get('local_flight') or state.get('departing_transport')
                if flight is not None and flight.get('plane_id') is not None:
                    view = flight.get('map_position')
                    if view is not None:
                        return view
            return original_get_plane()

        clientcom.getPlaneEntity = get_plane

    def status(owner, value):
        old = owner.avatarStatus
        if old != value:
            owner.avatarStatus = value
            owner.refreshAvatarStatus(old)
            log('Transport player status=%r->%r position=%r' % (old, value, owner.position))

    def release_plane(flight):
        plane_id = flight.get('plane_id')
        if plane_id is not None and BigWorld.entities.get(plane_id) is not None:
            BigWorld.destroyEntity(plane_id)
        flight['plane_id'] = None

    def move_plane(flight, elapsed):
        point = route_at(flight['route'], elapsed)
        flight['matrix'].translation = Math.Vector3(*point)
        if flight['phase'] != 'onboard' and elapsed >= flight['route']['duration']:
            log('Transport reached opposite map edge at %r' % (point,))
            release_plane(flight)

    def abort(flight, reason):
        owner = BigWorld.player()
        if owner is not None and owner.isOnPlane():
            owner.modelServer.leaveAttachModel()
            owner.boardingPlaneId = 0
            status(owner, types.AVATAR_STATUS_IN_COMBAT)
            owner.physics.teleport(Math.Vector3(*flight['return_position']))
        release_plane(flight)
        state.pop('local_flight', None)
        log('Transport aborted reason=%r' % reason)

    original_jump = getattr(commands, '_hypOriginalTransportJump', commands.startParachuting)
    commands._hypOriginalTransportJump = original_jump

    def jump():
        flight = state.get('local_flight')
        if not gameglobal.isOffline or flight is None:
            return original_jump()
        owner = BigWorld.player()
        if owner is None or not owner.isOnPlane() or flight.get('phase') != 'onboard':
            return False
        if time.time() - flight['started'] < 3:
            return False
        flight['phase'] = 'parachuting'
        flight['jumped_at'] = time.time()
        status(owner, types.AVATAR_STATUS_STARTPARACHUTING)
        owner.boardingPlaneId = 0
        log('Transport exit plane=%r position=%r' % (flight['plane_id'], owner.position))
        return True

    commands.startParachuting = jump

    def start(audit=False):
        owner = BigWorld.player()
        if owner is None or not owner.inWorld or state.get('local_flight') is not None:
            return False
        if owner.hp <= 0 or owner.inParachuting() or owner.isOnVehicle() or owner.isDoingAction or getattr(owner, 'climbCode', ''):
            return False
        origin = tuple(owner.position)
        yaw = BigWorld.dcursor().yaw
        height = origin[1] + 600
        route = map_route(state.get('battle_map_rect', DEFAULT_MAP_RECT), yaw, height)
        source, destination = route['source'], route['destination']
        previous = state.pop('departing_transport', None)
        if previous is not None:
            release_plane(previous)
        flight = {'phase': 'loading', 'route': route, 'yaw': yaw,
                  'return_position': origin, 'audit': audit, 'requested': time.time()}
        state['local_flight'] = flight
        try:
            plane_id = BigWorld.createEntity('Plane', owner.spaceID, 0,
                Math.Vector3(*source), Math.Vector3(0, 0, yaw),
                {'function': types.PLANE_FUNCTION_PARACHUTING})
            flight['plane_id'] = plane_id
            log('Transport created plane=%r source=%r destination=%r audit=%r' % (
                plane_id, source, destination, audit))
            return True
        except Exception:
            abort(flight, traceback.format_exc())
            return False

    state['start_transport_flight'] = start

    def update():
        if state.get('flight_generation') != generation:
            return
        flight = state.get('local_flight')
        try:
            departing = state.get('departing_transport')
            if departing is not None:
                move_plane(departing, time.time() - departing['started'])
                if departing['plane_id'] is None:
                    state.pop('departing_transport', None)
            if flight is not None:
                owner = BigWorld.player()
                plane_id = flight.get('plane_id')
                plane = BigWorld.entities.get(plane_id) if plane_id is not None else None
                if owner is None or not owner.inWorld:
                    abort(flight, 'player unavailable')
                elif flight['phase'] == 'loading':
                    if plane is None:
                        abort(flight, 'plane unavailable')
                    elif plane.model is not None and getattr(plane, 'firstModelFinish', False):
                        transform = Math.Matrix()
                        transform.setRotateY(flight['yaw'])
                        transform.translation = Math.Vector3(*flight['route']['source'])
                        servo = BigWorld.Servo(transform)
                        plane.model.addMotor(servo)
                        flight['matrix'], flight['servo'] = transform, servo
                        # The Servo moves the model, while the entity's network
                        # position remains at the first waypoint. Feed both
                        # original map widgets the same position as the model.
                        flight['map_position'] = PlaneMapPosition(plane, transform)
                        owner.initParachuteParams()
                        duration = flight['route']['duration']
                        points = [route_at(flight['route'], seconds) for seconds in (0, 3, duration * 0.9, duration)]
                        owner.planeRouteData = [{'pos': point} for point in points]
                        owner.boardingPlaneId = plane.id
                        pose = state.get('pose_servo')
                        if pose is not None and pose in owner.model.motors:
                            owner.model.delMotor(pose)
                        status(owner, types.AVATAR_STATUS_ONBOARD)
                        owner.onPlaneBoarding(plane.id)
                        if ui is not None:
                            try:
                                plane.setPlanePath()
                            except Exception:
                                log('Transport map display failed: %s' % traceback.format_exc())
                        # Feed both native following motors the same trajectory.
                        # Plane entities lack a server transform stream locally.
                        owner.physics.followTarget = transform
                        for motor in owner.model.motors:
                            if getattr(motor, '__name__', type(motor).__name__) == 'Follow':
                                motor.target = transform
                        flight['phase'] = 'onboard'
                        flight['started'] = time.time()
                        log('Transport boarded id=%r model=%r follow=%r' % (
                            plane.id, plane.model.sources, owner.physics.followTarget))
                    elif time.time() - flight['requested'] > 20:
                        abort(flight, 'plane model timeout')
                else:
                    elapsed = time.time() - flight['started']
                    if plane is not None:
                        move_plane(flight, elapsed)
                    if flight['phase'] == 'onboard':
                        if elapsed > 2 and not flight.get('motion_checked'):
                            expected = route_at(flight['route'], elapsed)
                            error = math.sqrt(sum((a-b)**2 for a,b in zip(owner.position, expected)))
                            flight['motion_checked'] = True
                            flight['motion_error'] = error
                            log('Transport native motion checked expected=%r player=%r model=%r error=%.2f' % (
                                expected, owner.position, plane.model.position, error))
                            if error > 25:
                                abort(flight, 'native following failed')
                        if state.get('local_flight') is flight and elapsed >= (5 if flight['audit'] else flight['route']['duration'] * 0.9):
                            jump()
                    elif flight['phase'] == 'parachuting':
                        if owner.avatarStatus in types.AVATAR_STATUS_PATACHUTE_OPEN_SET:
                            flight['parachute_opened'] = True
                        if not flight.get('camera_checked') and time.time() - flight['jumped_at'] > 2:
                            flight['camera_checked'] = True
                            camera_error = math.sqrt(sum((a-b)**2 for a,b in zip(owner.position, BigWorld.camera().position)))
                            flight['camera_error'] = camera_error
                            log('Transport freefall camera distance=%.2f' % camera_error)
                        if not owner.inParachuting():
                            log('Transport landed position=%r audit=%r' % (owner.position, flight['audit']))
                            if flight['audit']:
                                release_plane(flight)
                                state['transport_audit_result'] = {
                                    'landed': True, 'plane_removed': flight['plane_id'] is None,
                                    'motion_error': flight.get('motion_error'),
                                    'freefall_camera_error': flight.get('camera_error'),
                                    'parachute_opened': flight.get('parachute_opened', False)}
                                log('Transport selfcheck result=%r' % state['transport_audit_result'])
                                owner.physics.teleport(Math.Vector3(*flight['return_position']))
                            elif flight['plane_id'] is not None:
                                state['departing_transport'] = flight
                            state.pop('local_flight', None)
        except Exception:
            log('Transport update failed: %s' % traceback.format_exc())
            if flight is not None:
                abort(flight, 'update error')
        BigWorld.callback(0.0 if state.get('local_flight') or state.get('departing_transport') else 0.5, update)

    previous_key = player.handleKeyEvent.im_func
    keys = player.setVehiclePhysics.im_func.func_globals['keys']
    flight_key = getattr(keys, 'KEY_F9', None)
    if flight_key is not None:
        def handle_key(owner, is_down, key, mods):
            if gameglobal.isOffline and key == flight_key:
                if is_down:
                    start()
                return True
            return previous_key(owner, is_down, key, mods)
        type(player).handleKeyEvent = handle_key
    BigWorld.callback(0.2, update)
    log('Transport flight control ready: F9 board, F jump')
    if state.pop('transport_selfcheck_requested', False):
        def audit_start():
            if not start(audit=True) and state.get('local_flight') is not None:
                BigWorld.callback(3, audit_start)
        BigWorld.callback(1, audit_start)
