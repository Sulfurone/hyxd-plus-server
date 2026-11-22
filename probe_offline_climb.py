"""Read-only collision probes around an installed map window, inside the client."""
import math
import sys
import BigWorld
import Math


def probe(player, log):
    module = sys.modules['helpers.jumpClimbHelper']
    building = sys.modules['helpers.buildingPartsManager']
    windows = building.WINDD.data
    window_class = sys.modules['WindowOnline'].WindowOnline
    paths = window_class.enterWorld.im_func.func_globals['clientConst'].WINDOWPATH
    candidates = [pair for pair in windows.items()
                  if paths.get(pair[1]['modelFileName'], (None, None, False))[-1]]
    log('Vaultable glass-window definitions=%r totalWindowDefinitions=%r' % (len(candidates), len(windows)))
    nearest = sorted(candidates, key=lambda pair: sum((a-b)**2 for a, b in zip(pair[1]['position'], player.position)))[:2]

    class ModelProxy(object):
        pass

    class OwnerProxy(object):
        def __getattr__(self, name):
            return getattr(player, name)

    for window_id, data in nearest:
        point = Math.Vector3(*data['position'])
        base_yaw = data['direction'][2]
        normal = Math.Vector3(math.sin(base_yaw), 0, math.cos(base_yaw))
        for side in (-1, 1):
            for distance in (0.7,):
                position = point + normal * (side * distance)
                hit = BigWorld.collide(player.spaceID, (position.x, point.y + 0.1, position.z), (position.x, point.y - 4, position.z))
                if hit is None:
                    continue
                position.y = hit[0].y + 0.01
                owner = OwnerProxy()
                owner.position = position
                owner.model = ModelProxy()
                owner.model.yaw = base_yaw + (math.pi if side == 1 else 0)
                helper = module.JumpClimbHelper(owner)
                helper._hypProbeTrace = []
                method = getattr(module.JumpClimbHelper, '_hypOriginalGetClimbInfo', module.JumpClimbHelper.getClimbInfo)
                result = method(helper)
                log('Window climb geometry id=%r side=%r distance=%r position=%r yaw=%r result=%r detail=%r' % (
                    window_id, side, distance, position, owner.model.yaw, result, helper._hypProbeTrace))
