"""Original client characters used to exercise local combat and damage."""
import math
import traceback

import BigWorld
import Math
import PhysX
import game


def install(player, state, log):
    targets = state.setdefault('local_targets', {})
    for entity_id in list(targets):
        entity = BigWorld.entities.get(entity_id)
        if entity is None:
            del targets[entity_id]
        elif sum((a - b) ** 2 for a, b in zip(entity.position, player.position)) > 2500:
            BigWorld.destroyEntity(entity_id)
            del targets[entity_id]
            log('Local practice target removed outside nearby area id=%r' % entity_id)
        elif not targets[entity_id].get('native_health_initialized'):
            entity.maxHp = 10000
            entity.hp = 10000
            targets[entity_id]['native_health_initialized'] = True
    if targets or state.get('target_spawn_pending'):
        return
    state['target_spawn_pending'] = True

    def create_target():
        state['target_spawn_pending'] = False
        try:
            owner = BigWorld.player()
            if owner is None or not owner.inWorld:
                return
            origin = owner.position
            yaw = BigWorld.dcursor().yaw
            candidate = None
            for distance, offset in ((8, 0), (6, 0.7), (6, -0.7), (5, 1.4), (5, -1.4)):
                x = origin.x + math.sin(yaw + offset) * distance
                z = origin.z + math.cos(yaw + offset) * distance
                hit = BigWorld.collide(owner.spaceID, (x, origin.y + 2, z),
                                       (x, origin.y - 3, z))
                if hit is None or abs(hit[0].y - origin.y) > 1.5:
                    continue
                position = Math.Vector3(x, hit[0].y + 0.02, z)
                obstruction = BigWorld.collide(owner.spaceID,
                    (origin.x, origin.y + 1.3, origin.z),
                    (position.x, position.y + 1.3, position.z))
                if obstruction is None:
                    candidate = position
                    break
            if candidate is None:
                log('Local target deferred: no nearby visible level ground')
                return
            info = game.getOfflineAvatarInfo()
            info.update(hp=10000, maxHp=10000, avatarStatus=5)
            entity_id = BigWorld.createEntity('AvatarRobot', owner.spaceID, 0,
                candidate, Math.Vector3(0, yaw + math.pi, 0), info)
            target = BigWorld.entities.get(entity_id)
            if target is None:
                raise RuntimeError('Native target entity was not created')
            targets[entity_id] = {'spawn': tuple(candidate), 'ready': False,
                                  'native_health_initialized': True}
            log('Local target created id=%r position=%r type=%r' % (
                entity_id, target.position, type(target).__name__))

            def wait_for_model(attempt=0):
                entity = BigWorld.entities.get(entity_id)
                if entity is None:
                    targets.pop(entity_id, None)
                    return
                try:
                    sources = getattr(entity.model, 'sources', ()) if entity.model else ()
                    if sources and sources != ('effect/dummy/effectdummy.model',):
                        targets[entity_id]['ready'] = True
                        log('Local target ready id=%r hp=%r model=%r position=%r' % (
                            entity_id, entity.hp, sources, entity.position))

                        def probe_hitbox():
                            try:
                                target = BigWorld.entities.get(entity_id)
                                if target is None:
                                    return
                                position = target.position
                                start = Math.Vector3(position.x, position.y + 1.2, position.z - 3)
                                end = Math.Vector3(position.x, position.y + 1.2, position.z)
                                direction = end - start
                                direction.normalise()
                                cast = PhysX.raycast(start, end, direction, owner.shootExceptEntIds)
                                result = owner.hitEntity(cast, False)
                                log('Native target collision probe expected=%r result=%r' % (entity_id, result))
                            except Exception:
                                log('Native target collision probe failed: %s' % traceback.format_exc())

                        BigWorld.callback(0.5, probe_hitbox)
                        return
                    if attempt >= 20:
                        BigWorld.destroyEntity(entity_id)
                        targets.pop(entity_id, None)
                        log('Local target removed: original body did not load')
                        return
                    if attempt % 5 == 0:
                        entity.modelServer.bodyUpdateStatus = 0
                        entity.modelServer.bodyUpdate()
                    BigWorld.callback(1, lambda: wait_for_model(attempt + 1))
                except Exception:
                    log('Local target setup failed: %s' % traceback.format_exc())
                    BigWorld.destroyEntity(entity_id)
                    targets.pop(entity_id, None)

            BigWorld.callback(0.2, wait_for_model)
        except Exception:
            log('Local target creation failed: %s' % traceback.format_exc())

    BigWorld.callback(0.2, create_target)
