"""Original client characters used to exercise local combat and damage."""
import math
import traceback

import BigWorld
import Math
import PhysX
import game


def install(player, state, log):
    current_round = state.get('local_round')
    if current_round and current_round.get('phase') != 'active':
        return
    targets = state.setdefault('local_targets', {})
    for entity_id in list(targets):
        entity = BigWorld.entities.get(entity_id)
        if entity is None:
            if current_round and not targets[entity_id].get('defeated'):
                current_round['spawned'] = max(0, current_round['spawned'] - 1)
            del targets[entity_id]
        elif not current_round and sum((a - b) ** 2 for a, b in zip(entity.position, player.position)) > 2500:
            BigWorld.destroyEntity(entity_id)
            del targets[entity_id]
            log('Local practice target removed outside nearby area id=%r' % entity_id)
        elif not targets[entity_id].get('native_health_initialized'):
            entity.maxHp = 10000
            entity.hp = 10000
            targets[entity_id]['native_health_initialized'] = True
    if current_round and current_round['spawned'] >= current_round['total']:
        return
    if len(targets) >= state.get('local_enemy_count', 1) or state.get('target_spawn_pending'):
        return
    pending = object()
    state['target_spawn_pending'] = pending

    def release_budget(entity_id):
        info = targets.pop(entity_id, None)
        if (current_round and info and not info.get('defeated')
                and state.get('local_round') is current_round):
            current_round['spawned'] = max(0, current_round['spawned'] - 1)

    def create_target():
        if state.get('target_spawn_pending') is not pending:
            return
        state.pop('target_spawn_pending', None)
        if state.get('local_round') is not current_round:
            return
        if current_round and (current_round.get('phase') != 'active'
                or current_round['spawned'] >= current_round['total']):
            return
        try:
            owner = BigWorld.player()
            if owner is None or not owner.inWorld:
                return
            origin = owner.position
            yaw = BigWorld.dcursor().yaw
            candidate = None
            fallback, fallback_rank = None, None
            candidates = [(22, 0.6), (20, -0.7), (18, 1.4), (18, -1.4), (8, 0)]
            candidates.extend((distance, index * math.pi / 4)
                              for distance in (8, 16, 28, 40) for index in range(8))
            for distance, offset in candidates:
                x = origin.x + math.sin(yaw + offset) * distance
                z = origin.z + math.cos(yaw + offset) * distance
                hit = BigWorld.collide(owner.spaceID, (x, origin.y + 2, z),
                                       (x, origin.y - 3, z))
                if hit is None:
                    hit = BigWorld.collide(owner.spaceID, (x, origin.y + 12, z),
                                           (x, origin.y - 60, z))
                if hit is None or abs(hit[0].y - origin.y) > 60:
                    continue
                position = Math.Vector3(x, hit[0].y + 0.02, z)
                if any(sum((a-b)**2 for a,b in zip(position, entity.position)) < 25
                       for entity in (BigWorld.entities.get(entity_id) for entity_id in targets)
                       if entity is not None):
                    continue
                obstruction = BigWorld.collide(owner.spaceID,
                    (origin.x, origin.y + 1.3, origin.z),
                    (position.x, position.y + 1.3, position.z))
                if obstruction is None and abs(hit[0].y - origin.y) <= 1.5:
                    candidate = position
                    break
                # Roofs and slopes may have no level, visible point. Keep an
                # original grounded entity nearby rather than blocking the
                # entire finite round until the player changes elevation.
                rank = (obstruction is not None, abs(hit[0].y - origin.y), distance)
                if fallback_rank is None or rank < fallback_rank:
                    fallback, fallback_rank = position, rank
            if candidate is None:
                candidate = fallback
            if candidate is None:
                log('Local target deferred: no nearby supported spawn position')
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
            if current_round:
                targets[entity_id]['round_id'] = current_round['id']
                current_round['spawned'] += 1
            log('Local target created id=%r position=%r type=%r' % (
                entity_id, target.position, type(target).__name__))

            def wait_for_model(attempt=0):
                entity = BigWorld.entities.get(entity_id)
                if entity is None or entity_id not in targets:
                    release_budget(entity_id)
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
                        release_budget(entity_id)
                        log('Local target removed: original body did not load')
                        return
                    if attempt % 5 == 0:
                        entity.modelServer.bodyUpdateStatus = 0
                        entity.modelServer.bodyUpdate()
                    BigWorld.callback(1, lambda: wait_for_model(attempt + 1))
                except Exception:
                    log('Local target setup failed: %s' % traceback.format_exc())
                    BigWorld.destroyEntity(entity_id)
                    release_budget(entity_id)

            BigWorld.callback(0.2, wait_for_model)
        except Exception:
            log('Local target creation failed: %s' % traceback.format_exc())

    BigWorld.callback(0.2, create_target)
