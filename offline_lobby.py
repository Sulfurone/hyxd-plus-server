"""Connect the installed client's lobby controls to local play."""
import traceback

import BigWorld
import gameglobal


LOBBY_WIDGETS = ('lobbyButton', 'matchLobby', 'navigationButton')
COMBAT_WIDGETS = ('latencyInfo', 'pressKeyF', 'equipmentInfo', 'tacticalBattle',
                  'playerPose', 'aimCross', 'playerBlood', 'holdBreath', 'compass',
                  'littleMap', 'teamFrame', 'teamIndicator', 'aliveInfo', 'beHitHelp',
                  'soundDetection', 'hotkeyBlackHud', 'hotkeyWhiteHud', 'weaponInfo',
                  'weaponSlot', 'boardBar', 'playerChat', 'shootWarning', 'killInfoMsg',
                  'combatMsgList', 'inventory', 'gasStatusBar', 'matchResult')


def override(proxy, name, local, active):
    cls = proxy.__class__
    saved = '_hypLobbyOriginal_' + name
    original = getattr(cls, saved, getattr(cls, name))
    setattr(cls, saved, original)

    def method(instance, *args, **kwargs):
        if gameglobal.isOffline and active():
            return local(instance, *args, **kwargs)
        return original(instance, *args, **kwargs)

    setattr(cls, name, method)


def install(player, state, log):
    ui = gameglobal.rds.ui

    def in_lobby():
        return state.get('lobby_active', False)

    def close_proxies(names):
        # Avoid loading a proxy solely to close it.
        for name in names:
            proxy = ui.__dict__.get(name)
            if proxy is not None:
                proxy.hide()

    def leave_scene():
        state['lobby_active'] = False
        close_proxies(('gameSetting',) + LOBBY_WIDGETS)
        player.ap.changeCursor()
        ui.initHud()

    def show(exit_vehicle=True):
        if in_lobby():
            return True
        if (not player.inWorld or not player.isOnLoaded or player.isOnPlane()
                or player.inParachuting() or state.get('local_flight')
                or state.get('drop_pending') or getattr(player, 'climbCode', '')):
            return False
        if player.isOnVehicle():
            if not exit_vehicle or state.get('lobby_vehicle_exit'):
                return False
            commands = player.pickUse.im_func.func_globals['serverCmd']
            if not commands.vApplyLeave(None):
                return False
            state['lobby_vehicle_exit'] = True

            def after_exit():
                state.pop('lobby_vehicle_exit', None)
                show(exit_vehicle=False)

            BigWorld.callback(0.5, after_exit)
            return True
        state['lobby_active'] = True
        try:
            # Keep the battle space and camera mounted. Alternating it with the
            # separate interface space drops terrain meshes on this client.
            player.ap.resetControl()
            state['ai_enabled'] = False
            state.pop('player_respawn_at', None)
            ui.gasStatusBar.reset()
            close_proxies(COMBAT_WIDGETS)
            ui.loginView.hide()
            for name in LOBBY_WIDGETS:
                getattr(ui, name).show()
            player.ap.restore(False)
            state['lobby_visited'] = True
            log('Local lobby ready; kept battle camera and terrain in space=%r' % player.spaceID)
            return True
        except Exception:
            log('Local lobby show failed: %s' % traceback.format_exc())
            try:
                leave_scene()
            except Exception:
                log('Local lobby rollback failed: %s' % traceback.format_exc())
            return False

    def start(transport=True):
        if not in_lobby():
            return False
        try:
            leave_scene()
            if not transport:
                state.pop('local_round', None)
                recovered = state['respawn_local_player']()
                if recovered:
                    log('Local lobby started practice')
                    return True
            elif state['start_local_round']():
                log('Local lobby started solo round')
                return True
        except Exception:
            log('Local lobby start failed: %s' % traceback.format_exc())
        show()
        return False

    def solo_mode(owner):
        return match_constants.BATTLE_ROYALE_MODE_ONE, False

    def is_solo(owner):
        return True

    def match_ready(proxy):
        if proxy.widget is not None:
            proxy.widget.startMatchBtn.visible = True
            proxy.widget.cancelMatchBtn.visible = False
            proxy.refreshMatchModeBtn()
            proxy.widget.matchMode.enabled = False
            proxy.widget.matchMode.canMergeMc.visible = False

    def match_click(proxy, event=None):
        BigWorld.callback(0, start)

    def lobby_tabs(proxy):
        if proxy.widget is None:
            return
        proxy.widget.lobbyBtn.selected = True
        for name in ('characterBtn', 'rankBtn', 'marketBtn', 'boxBtn'):
            btn = getattr(proxy.widget, name, None)
            if btn is not None:
                btn.visible = False

    def navigation(proxy):
        if proxy.widget is None:
            return
        for name in ('securityBtn', 'signInBtn', 'activityBtn', 'announceBtn',
                     'friendBtn', 'mailBoxBtn', 'wikiBtn', 'activityHint', 'activityHintOfWiki'):
            btn = getattr(proxy.widget, name, None)
            if btn is not None:
                btn.visible = False

    def return_from_result(proxy, event=None):
        BigWorld.callback(0, show)

    def has_local_result():
        current = state.get('local_round')
        return current is not None and current.get('phase') == 'finished'

    result = ui.matchResult
    original_result_init = getattr(result.__class__, '_hypLobbyOriginal_initUI', result.__class__.initUI)

    def result_init(proxy):
        original_result_init(proxy)
        if proxy.widget is not None:
            controls = proxy.widget.pageCtrlMc
            controls.visible = True
            controls.showRankBtn.visible = False
            controls.showCardBtn.visible = False

    match = ui.matchLobby
    match_constants = match.refreshMatchModeBtn.im_func.func_globals['const']
    player_class = type(player)
    for name, value in (('getGameMatchMode', solo_mode), ('isSinglePlayer', is_solo), ('isHeader', is_solo)):
        if not hasattr(player_class, name):
            setattr(player_class, name, value)
    for name, value in (('member', {}), ('singleReady', False), ('groupNUID', 0)):
        if not hasattr(player, name):
            setattr(player, name, value)
    override(match, 'refreshInfo', match_ready, in_lobby)
    override(match, '_onStartMatchBtnClick', match_click, in_lobby)
    override(match, '_onMatchModeClick', lambda proxy, event: None, in_lobby)
    override(ui.lobbyButton, 'refreshUI', lobby_tabs, in_lobby)
    override(ui.lobbyButton, '_onLobbyBtnClick', lambda proxy, event=None: None, in_lobby)
    override(ui.navigationButton, 'initUI', navigation, in_lobby)
    override(result, 'initUI', result_init, has_local_result)
    override(result, '_onGoBackBtnClick', return_from_result, has_local_result)
    override(player.ap, 'uiNeedShowCursor', lambda controller: True, in_lobby)

    keys = player.setVehiclePhysics.im_func.func_globals['keys']
    current_key = player.handleKeyEvent.im_func
    if current_key is state.get('lobby_key_handler'):
        previous_key = state['lobby_previous_key']
    else:
        previous_key = current_key

    def handle_key(owner, is_down, key, mods):
        if not gameglobal.isOffline:
            return previous_key(owner, is_down, key, mods)
        if key == keys.KEY_F11:
            if is_down:
                show()
            return True
        if in_lobby():
            if is_down and key == keys.KEY_F10:
                start()
            elif is_down and key == keys.KEY_F6:
                start(transport=False)
            return True
        return previous_key(owner, is_down, key, mods)

    player_class.handleKeyEvent = handle_key
    state['lobby_previous_key'] = previous_key
    state['lobby_key_handler'] = handle_key
    state['show_local_lobby'] = show
    state['start_from_local_lobby'] = start
    return show
