"""Explicit unattended runtime check; this is not run during normal startup."""
import math
import traceback
import copy
import uuid

import BigWorld
import gameglobal
import PhysX


def run(state, log):
    owner = BigWorld.player()
    enemies = [BigWorld.entities.get(entity_id) for entity_id in state.get('local_targets', {})]
    enemy = next((entity for entity in enemies if entity is not None and entity.hp > 0), None)
    if enemy is None:
        log('Native battle selfcheck deferred: no living target')
        return
    previous_enabled = state.get('ai_enabled', True)
    state['ai_enabled'] = False
    state['respawn_local_player']()
    dc = BigWorld.dcursor()
    previous_angles = (dc.yaw, dc.pitch)
    previous_attack = owner.ap.fireLeftKeyDown
    original_inventory = owner.clientEquipsAndInv
    gun_state = []

    def restore():
        try:
            owner.ap.fireLeftKeyDown = previous_attack
            gun = owner.getActivedWeapon()
            if gun is not None:
                gun.cancelPrimaryAttackCB()
                if gun_state:
                    gun.isBulletLoaded, gun.gunState = gun_state[0]
        finally:
            owner.clientEquipsAndInv = original_inventory
            state['ai_enabled'] = previous_enabled
            state.pop('battle_ray_audit', None)
            owner.refreshCurrentAmmo()
            gameglobal.rds.cam.cc.deltaYaw = 0
            dc.yaw = previous_angles[0]
            dc.pitch = previous_angles[1]

    def aim():
        try:
            gun = owner.getActivedWeapon()
            if gun is None:
                raise RuntimeError('No weapon after native respawn')
            gun_state.append((gun.isBulletLoaded, gun.gunState))
            # Use an isolated native inventory for this diagnostic magazine.
            # Restore the player's real inventory after exercising real fire.
            owner.clientEquipsAndInv = copy.deepcopy(original_inventory)
            inv = owner.clientEquipsAndInv
            native = inv.loadAmmo.im_func.func_globals
            ammo_slot = native['combatconst'].EQUIPMENT_SUBSLOT_AMMO
            capacity = inv.calcAmmoCapacity(gun.slotId)
            magazine = inv.getItemInEquipment(gun.slotId, ammo_slot)
            if magazine is None:
                inv.insertItemToEquipment(native['Item'](int(uuid.uuid4().hex[:7], 16), gun.bulletType, capacity, {}),
                                          gun.slotId, ammo_slot)
            elif magazine.cwrap < capacity:
                inv.equipments[gun.slotId].addAmmo(capacity - magazine.cwrap)
            owner.onLoadAmmo(capacity, gun.slotId)
            log('Native battle selfcheck isolated magazine capacity=%r' % capacity)
            point = enemy.getAvatarModel().node('biped Spine1').position
            source = owner.getAvatarModel().node('biped Head').position
            delta = point - source
            yaw = math.atan2(delta.x, delta.z)
            pitch = math.atan2(delta.y, math.sqrt(delta.x**2 + delta.z**2))
            gameglobal.rds.cam.cc.deltaYaw = 0
            dc.yaw = yaw
            dc.pitch = pitch
            ray = point - source
            ray.normalise()
            cast = PhysX.raycast(source, point + ray * 0.5, ray, owner.shootExceptEntIds)
            log('Native battle direct collision expected=%r ignored=%r hit=%r' % (
                enemy.id, owner.shootExceptEntIds, owner.hitEntity(cast, False)))
            log('Native battle selfcheck aim source=%r target=%r canFire=%r clip=%r' % (
                source, point, gun.canPrimaryAttack(), gun.bulletCnt))
            native = gun.canPrimaryAttack.im_func.func_globals
            log('Native battle fire gates=%r' % {
                'clip': gun.bulletCnt, 'reserve': gun.bulletInBag,
                'loaded': gun.isBulletLoaded, 'gunState': gun.gunState,
                'nextAttack': gun.nextPrimaryAttackTime, 'now': native['utils'].getTrueNow(),
                'inspect': owner.isIntrospectionMode, 'pullingBolt': owner.inPullingBolt(),
                'canShootStatus': owner.checkStatus(native['const'].CT_SHOOT),
                'action': owner.fashion.getDoingActionType(), 'life': owner.lifeCode})

            def shoot():
                try:
                    before_ammo, before_hp = gun.bulletCnt, enemy.hp
                    owner.ap.fireLeftKeyDown = True
                    state['battle_ray_audit'] = True
                    gun.tryPrimaryAttack()

                    def finish():
                        try:
                            result = {'ammo_before': before_ammo, 'ammo_after': gun.bulletCnt,
                                      'target_hp_before': before_hp, 'target_hp_after': enemy.hp,
                                      'player_alive': not owner.isDead()}
                            result['passed'] = gun.bulletCnt < before_ammo and enemy.hp < before_hp and result['player_alive']
                            state['battle_selfcheck_result'] = result
                            log('Native battle selfcheck result=%r' % result)
                        finally:
                            restore()

                    BigWorld.callback(1.5, finish)
                except Exception:
                    log('Native battle selfcheck shooting failed: %s' % traceback.format_exc())
                    restore()

            def align_camera(attempt=0):
                point = enemy.getAvatarModel().node('biped Spine1').position
                delta = point - BigWorld.camera().position
                gameglobal.rds.cam.cc.deltaYaw = 0
                dc.yaw = math.atan2(delta.x, delta.z)
                dc.pitch = math.atan2(delta.y, math.sqrt(delta.x**2 + delta.z**2))
                if attempt < 3:
                    BigWorld.callback(0.3, lambda: align_camera(attempt + 1))
                else:
                    BigWorld.callback(0.3, shoot)

            BigWorld.callback(0.3, align_camera)
        except Exception:
            log('Native battle selfcheck failed: %s' % traceback.format_exc())
            restore()

    BigWorld.callback(1.5, aim)
