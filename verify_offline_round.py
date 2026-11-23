"""Explicit unattended original-client check of circle, loss, victory and restart."""
import time
import traceback

import BigWorld
import gameglobal


def run(state, log):
    owner = BigWorld.player()
    result = {}
    started = time.time()

    def cleanup():
        state.pop('local_round', None)
        state.pop('player_respawn_at', None)
        state['ai_enabled'] = False
        gameglobal.rds.ui.matchResult.hide()
        gameglobal.rds.ui.gasStatusBar.reset()
        gameglobal.rds.ui.gasStatusBar.hide()
        state['respawn_local_player']()
        state['round_selfcheck_result'] = result
        log('Local round selfcheck result=%r' % result)

    def guarded(callback):
        def invoke():
            try:
                if time.time() - started > 300:
                    raise RuntimeError('Round selfcheck timed out')
                callback()
            except Exception:
                result['error'] = traceback.format_exc()
                log('Local round selfcheck failed: %s' % result['error'])
                cleanup()
        return invoke

    def transport_landed():
        current = state['local_round']
        if state.get('local_flight') or owner.inParachuting() or current['phase'] == 'deploying':
            BigWorld.callback(1, guarded(transport_landed))
            return
        result['transport_round_landed'] = current['phase'] == 'active'
        result['transport'] = state.get('transport_audit_result')
        assert result['transport_round_landed']
        assert result['transport']['parachute_opened']
        assert result['transport']['plane_removed']
        result['passed'] = True
        cleanup()

    def verify_budget():
        current = state['local_round']
        living = [BigWorld.entities.get(entity_id) for entity_id in state.get('local_targets', {})]
        result['finite_budget'] = current['spawned'] == current['total'] and not any(
            entity is not None and entity.hp > 0 for entity in living)
        assert result['finite_budget']
        previous_id = current['id']
        keys = owner.setVehiclePhysics.im_func.func_globals['keys']
        owner.handleKeyEvent(True, keys.KEY_F10, 0)
        assert state['local_round']['id'] != previous_id
        result['f10_restart'] = state['local_round']['total'] == 8
        assert result['f10_restart']
        state['ai_enabled'] = False
        flight = state.get('local_flight')
        assert flight is not None
        flight['audit'] = True
        BigWorld.callback(1, guarded(transport_landed))

    def check_shooting():
        check = state.get('battle_selfcheck_result')
        if check is None:
            BigWorld.callback(1, guarded(check_shooting))
            return
        result['native_shooting'] = check
        current = state['local_round']
        assert check['passed']
        result['native_bursts'] = result.get('native_bursts', 0) + 1
        if check['target_hp_after'] > 0:
            # Native recoil and limb hits may need more than one burst.
            BigWorld.callback(1, guarded(wait_enemy))
            return
        result['targets_shot'] = result.get('targets_shot', 0) + 1
        if current['phase'] == 'active':
            BigWorld.callback(1, guarded(wait_enemy))
            return
        assert current['phase'] == 'finished' and current['won']
        assert current.get('result_ui_error') is None
        gameglobal.rds.ui.matchResult.initState()
        result['victory'] = True
        result['victory_widget'] = gameglobal.rds.ui.matchResult.widget is not None
        result['victory_kills'] = current['result']['member'][owner.gbId]['killSum']
        assert result['victory_kills'] == 8
        BigWorld.callback(9, guarded(verify_budget))

    def wait_enemy():
        live = [(entity_id, info) for entity_id, info in state.get('local_targets', {}).items()
                if info.get('ready') and BigWorld.entities.get(entity_id) is not None
                and BigWorld.entities.get(entity_id).hp > 0]
        if not live:
            scope = {}
            execfile('I:/hyxd/hyp/_local_server/offline_targets.py', scope)
            scope['install'](owner, state, log)
            BigWorld.callback(1, guarded(wait_enemy))
            return
        state.pop('battle_selfcheck_result', None)
        scope = {}
        execfile('I:/hyxd/hyp/_local_server/verify_offline_battle.py', scope)
        scope['run'](state, log)
        BigWorld.callback(1, guarded(check_shooting))

    def restart():
        current = state['local_round']
        result['death_stays_dead'] = owner.hp == 0 and owner.isDead()
        result['loss_widget'] = gameglobal.rds.ui.matchResult.widget is not None
        assert result['death_stays_dead'] and current['reason'] == 'safe_zone'
        assert current.get('result_ui_error') is None
        gameglobal.rds.ui.matchResult.initState()
        assert state['start_local_round'](transport=False, total=8)
        state['ai_enabled'] = False
        result['restart_alive'] = owner.hp == owner.maxHp and not owner.isDead()
        assert result['restart_alive']
        BigWorld.callback(1, guarded(wait_enemy))

    def begin():
        assert state['start_local_round'](transport=False, total=1)
        state['ai_enabled'] = False
        current = state['local_round']
        stage = current['circles'][0]
        current['stage_started'] = time.time() - stage[2] - stage[3] / 2.0
        state['tick_local_round'](False)
        proxy = gameglobal.rds.ui.gasStatusBar
        result['circle'] = (proxy.bCircleStartR, proxy.wCircleR, proxy.isShrinking)
        assert result['circle'] == (stage[0], stage[1], True)
        # Force damage with a diagnostic circle while keeping the player put.
        center = (owner.position.x + 1000, owner.position.z)
        current['circles'][0] = (100, 50) + stage[2:5] + (center, center)
        current['zone_event'] = None
        current['last_damage'] = 0
        owner.hp = 50
        owner.power = 10000
        state['tick_local_round'](False)
        assert current['phase'] == 'finished' and not current['won']
        result['zone_loss'] = True
        BigWorld.callback(4, guarded(restart))

    guarded(begin)()
