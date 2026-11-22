"""Use the original item animation and cast lifecycle for local medicine."""
import time
import traceback
import uuid

import BigWorld
import gameglobal


def install(player, state, log, commands, drug_defs, refresh_inventory):
    action_globals = player.startActionProgress.im_func.func_globals
    const = action_globals['const'] if 'const' in action_globals else player.endActionProgress.im_func.func_globals['const']

    def cancel(cause=0):
        token = state.pop('active_spell_token', None)
        item_uuid = state.pop('active_spell_uuid', None)
        count = state.pop('active_spell_num', 1)
        spell_id = state.pop('active_spell_action', None)
        if token is None:
            return False
        owner = BigWorld.player()
        if owner is not None:
            try:
                if spell_id is not None:
                    owner.endActionProgress(False, spell_id)
            finally:
                owner.useItemEndSpell(False, item_uuid, count)
        log('Local medicine cancelled uuid=%r cause=%r' % (item_uuid, cause))
        return True

    # Old timers share this dictionary. Invalidate them before installing the
    # new callbacks, so a reload during a cast cannot heal or consume twice.
    cancel('reload')
    original_cancel = getattr(commands, '_hypOriginal_cancelAction', commands.cancelAction)
    original_use = getattr(commands, '_hypOriginal_useCommonItem', commands.useCommonItem)
    commands._hypOriginal_cancelAction = original_cancel
    commands._hypOriginal_useCommonItem = original_use

    def cancel_action(cause=0):
        if not gameglobal.isOffline:
            return original_cancel(cause)
        return cancel(cause)

    def use_item(item_uuid, num=1):
        if not gameglobal.isOffline:
            return original_use(item_uuid, num)
        try:
            owner = BigWorld.player()
            it = owner.clientEquipsAndInv.getItemInInv(item_uuid)
            count = int(num)
            if it is None or count != 1 or it.cwrap < count or owner.hp <= 0:
                return False
            drug = drug_defs.get(it.itemId)
            if drug is None or not owner.useItemOfDrugChecker(it) or owner.inParachuting():
                return False
            cancel('restart')
            spell_id = drug.get('csmItemAction', it.itemId)
            period = max(0.1, float(drug.get('spellTime', 0)))
            token = uuid.uuid4().hex
            state.update(active_spell_token=token, active_spell_uuid=item_uuid,
                         active_spell_num=count, active_spell_action=spell_id)
            start_position = tuple(owner.position)
            started = time.time()
            owner.useItemBeginSpell(item_uuid, it.itemId, count)
            owner.startActionProgress(period, spell_id, owner.id, True, owner.yaw, const.ACT_USING_ITEM)
            log('Local medicine animation started item=%r action=%r seconds=%r doing=%r' % (
                it.itemId, spell_id, period, owner.isDoingAction))

            def tick():
                if state.get('active_spell_token') != token:
                    return
                try:
                    current = BigWorld.player()
                    if current is not owner or not owner.inWorld or owner.hp <= 0:
                        cancel('unavailable')
                        return
                    moved = sum((a - b) ** 2 for a, b in zip(start_position, owner.position))
                    if moved > 0.04 or owner.inParachuting() or not owner.isDoingAction:
                        cancel('interrupted')
                        return
                    remaining = period - (time.time() - started)
                    if remaining > 0:
                        BigWorld.callback(min(0.1, remaining), tick)
                        return
                    inv = owner.clientEquipsAndInv
                    current_item = inv.getItemInInv(item_uuid)
                    if current_item is None or current_item.cwrap < count:
                        cancel('missing_item')
                        return
                    inv.removeInvItem(item_uuid, count)
                    heal = drug.get('hpRecoveryImmediately', 0)
                    old_hp = owner.hp
                    cap = min(owner.maxHp, drug.get('maxRecovery', owner.maxHp))
                    if heal and old_hp < cap:
                        owner.hp = min(cap, old_hp + heal)
                        owner.set_hp(old_hp)
                    energy = drug.get('energyRecovery', 0)
                    if energy:
                        old_power = owner.power
                        owner.power = min(getattr(owner, 'maxPower', 10000), old_power + energy)
                        owner.set_power(old_power)
                    for key in ('active_spell_token', 'active_spell_uuid', 'active_spell_num', 'active_spell_action'):
                        state.pop(key, None)
                    try:
                        owner.endActionProgress(True, spell_id)
                    finally:
                        owner.useItemEndSpell(True, item_uuid, count)
                        refresh_inventory(owner)
                    log('Local medicine animation finished item=%r hp=%r power=%r' % (
                        it.itemId, owner.hp, getattr(owner, 'power', None)))
                except Exception:
                    log('Local medicine update failed: %s' % traceback.format_exc())
                    cancel('error')

            BigWorld.callback(0.1, tick)
            return True
        except Exception:
            log('Local medicine start failed: %s' % traceback.format_exc())
            cancel('error')
            return False

    commands.cancelAction = cancel_action
    commands.useCommonItem = use_item
    log('Local native medicine animation bridge ready')
