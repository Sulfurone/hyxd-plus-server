# -*- coding: utf-8 -*-
"""Connect the installed client's lobby controls to local play."""
import copy
import traceback

import BigWorld
import GUI
import Math
import ResMgr
import game
import gameglobal


BACKGROUND_TEXTURE = '../res/offline-lobby-background.dds'
LOBBY_WIDGETS = ('lobbyButton', 'matchLobby', 'navigationButton')
COMBAT_WIDGETS = ('latencyInfo', 'pressKeyF', 'equipmentInfo', 'tacticalBattle',
                  'playerPose', 'aimCross', 'playerBlood', 'holdBreath', 'compass',
                  'littleMap', 'teamFrame', 'teamIndicator', 'aliveInfo', 'beHitHelp',
                  'soundDetection', 'hotkeyBlackHud', 'hotkeyWhiteHud', 'weaponInfo',
                  'weaponSlot', 'boardBar', 'playerChat', 'shootWarning', 'killInfoMsg',
                  'combatMsgList', 'inventory', 'gasStatusBar', 'matchResult',
                  'parachuting')


def hide_character(state):
    preview = state.pop('lobby_character', None)
    if preview is None:
        return
    actor_id = preview.get('actor_id')
    if actor_id is not None and BigWorld.entities.get(actor_id) is not None:
        BigWorld.destroyEntity(actor_id)
    if 'image' in preview:
        GUI.delRoot(preview['image'])
        preview['adaptor'].attachment = None


def show_character(player, state, log):
    if state.get('lobby_character') is not None:
        return
    preview = state['lobby_character'] = {}

    def failed():
        log('Local lobby character preview failed: %s' % traceback.format_exc())
        hide_character(state)

    try:
        scene = game.lobbyScene.getManagerInstance().lobbyScene
        constants = scene.addActor.im_func.func_globals['clientConst']
        info = {'gbId': player.gbId, 'roleName': player.roleName or 'Local Player',
                'physique': copy.deepcopy(player.physique),
                'battleAspect': copy.deepcopy(player.battleAspect),
                'lobbyType': constants.LOBBY_TYPE_MAIN, 'lobbyState': constants.LOBBY_STATE_IDLE}
        position = player.position + Math.Vector3(12, 4, 0)
        preview['actor_id'] = BigWorld.createEntity('AvatarLobbyActor', player.spaceID, 0,
                                                    position, Math.Vector3(0, 0, 0), info)
    except Exception:
        failed()
        return

    def refresh():
        if state.get('lobby_character') is not preview:
            return
        try:
            resolution = tuple(GUI.screenResolution())
            if resolution != preview.get('resolution'):
                width, height = resolution
                preview['image'].size = (1.65 * height / width, 1.65)
                preview['resolution'] = resolution
            preview['adaptor'].takePhoto(1, 0, False, False, '', 0, 1, 0, 1)
            BigWorld.callback(1.0 / 24, refresh)
        except Exception:
            failed()

    def finish_model(attempts=50):
        if state.get('lobby_character') is not preview:
            return
        try:
            actor = BigWorld.entities.get(preview['actor_id'])
            if actor is None or not actor.firstModelFinish:
                if not attempts:
                    raise RuntimeError('Lobby character model load timed out')
                BigWorld.callback(0.1, lambda: finish_model(attempts - 1))
                return
            # The native lobby actor initializes the skeleton, actions and
            # materials. Keep its model only, in a separate portrait renderer.
            model = actor.model
            BigWorld.destroyEntity(preview['actor_id'])
            del preview['actor_id']
            model.position = (0, 0, 0)
            model.yaw = 0
            model.scale = (1, 1, 1)
            model.visible = True

            adaptor = GUI.MeshAdaptor('gui/photoGen/defaultMask.tga', 0)
            adaptor.setSize(768)
            adaptor.setLightDir(1, 0, 5)
            adaptor.setLights(0xffffff, 0xfffff3)
            adaptor.setLightHDR(2, 3)
            adaptor.modelAlpha = True
            adaptor.mixTextureAlpha = False
            adaptor.photoFov = 0.3
            adaptor.nearPlane = 2.0
            adaptor.farPlane = 50.0
            transform = Math.Matrix()
            transform.lookAt((0, 0.9, 7), (0, 0, -1), (0, 1, 0))
            adaptor.transform = transform
            adaptor.attachment = model
            try:
                model.action('special_idle_relax')(0, None, 0)
            except (ValueError, EnvironmentError):
                pass

            image = GUI.Simple('')
            image.position = (0, -0.07, 0.4)
            image.materialFX = 'BLEND'
            image.texture = adaptor.texture
            GUI.addRoot(image)
            preview.update(image=image, adaptor=adaptor)
            refresh()
            log('Local lobby character displayed')
        except Exception:
            failed()

    BigWorld.callback(0.1, finish_model)


def show_background(player, state, log):
    if state.get('lobby_background') is not None or not ResMgr.fileExist(BACKGROUND_TEXTURE):
        if state.get('lobby_background_visible'):
            show_character(player, state, log)
        return
    background = GUI.Simple(BACKGROUND_TEXTURE)
    background.position = (0, 0, 0.5)
    background.size = (2, 2)
    background.materialFX = 'SOLID'
    # Exclude the window caption and the pointer at the edge of the capture.
    top, right = 23.0 / 1040.0, 0.985
    background.mapping = ((0.0, top), (0.0, 1.0), (right, 1.0), (right, top))
    state['lobby_background'] = background

    def display(attempts=20):
        if state.get('lobby_background') is not background:
            return
        if background.texture.getWidth() < 16:
            if attempts:
                BigWorld.callback(0.1, lambda: display(attempts - 1))
            else:
                state.pop('lobby_background', None)
                log('Local lobby background texture could not load')
            return
        GUI.enable(True)
        GUI.addRoot(background)
        state['lobby_background_visible'] = True
        BigWorld.worldDrawEnabled(False)
        log('Local lobby background displayed')
        show_character(player, state, log)

    display()


def hide_background(state):
    background = state.pop('lobby_background', None)
    if state.pop('lobby_background_visible', False):
        GUI.delRoot(background)
        BigWorld.worldDrawEnabled(True)


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
        hide_character(state)
        hide_background(state)
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
            # Keep the battle space mounted while presenting its separate menu.
            player.ap.resetControl()
            state['ai_enabled'] = False
            state.pop('player_respawn_at', None)
            ui.gasStatusBar.reset()
            close_proxies(COMBAT_WIDGETS)
            ui.loginView.hide()
            for name in LOBBY_WIDGETS:
                getattr(ui, name).show()
            player.ap.restore(False)
            show_background(player, state, log)
            state['lobby_visited'] = True
            log('Local lobby ready space=%r' % player.spaceID)
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

    def refresh_mode(proxy):
        if proxy.widget is None:
            return
        mode = proxy.widget.matchMode
        selected = '自由练习' if state.get('lobby_practice') else '单人对局'
        mode.labels = ['本地模式', selected]
        mode.enabled = True
        mode.canMergeMc.visible = False

    def change_mode(proxy, event=None):
        state['lobby_practice'] = not state.get('lobby_practice', False)
        proxy.refreshMatchModeBtn()

    def match_click(proxy, event=None):
        transport = not state.get('lobby_practice', False)
        BigWorld.callback(0, lambda: start(transport))

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
    native_mode = getattr(type(match), '_hypLobbyOriginal_refreshMatchModeBtn', type(match).refreshMatchModeBtn)
    match_constants = native_mode.im_func.func_globals['const']
    player_class = type(player)
    for name, value in (('getGameMatchMode', solo_mode), ('isSinglePlayer', is_solo), ('isHeader', is_solo)):
        if not hasattr(player_class, name):
            setattr(player_class, name, value)
    for name, value in (('member', {}), ('singleReady', False), ('groupNUID', 0)):
        if not hasattr(player, name):
            setattr(player, name, value)
    override(match, 'refreshInfo', match_ready, in_lobby)
    override(match, 'refreshMatchModeBtn', refresh_mode, in_lobby)
    override(match, '_onStartMatchBtnClick', match_click, in_lobby)
    override(match, '_onMatchModeClick', change_mode, in_lobby)
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
    if in_lobby():
        match.refreshInfo()
        navigation(ui.navigationButton)
        close_proxies(COMBAT_WIDGETS)
        show_background(player, state, log)
    return show
