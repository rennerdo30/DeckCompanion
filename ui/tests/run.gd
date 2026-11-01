extends SceneTree
const Backend = preload("res://scripts/backend.gd")
var Nav = preload("res://scripts/navigation.gd")
var checks := 0
var failures := 0

func check(value: bool, description: String) -> void:
	checks += 1
	if not value:
		failures += 1
		printerr("FAIL: " + description)

func _initialize() -> void:
	call_deferred("run")

func run() -> void:
	print("Test process PID: ", OS.get_process_id())
	root.size = Vector2i(1280,800)
	if "--mutate-navigation" in OS.get_cmdline_user_args():
		var mutated := GDScript.new()
		mutated.source_code = FileAccess.get_file_as_string("res://scripts/navigation.gd").replace("index + direction", "index - direction")
		check(mutated.reload() == OK,"mutation loads")
		Nav = mutated
	var backend := Backend.new()
	root.add_child(backend)
	backend.mock = true
	var original := Backend.fixture("games")
	check(Backend.parse_response(JSON.stringify(original),0,"games").has("games"),"fixture parses")
	for raw in ["not JSON","[]","{}","{\"error\":4}","{\"games\":[null]}"]:
		check(Backend.parse_response(raw,0,"games").has("error"),"reject malformed response: " + raw)
	check(Backend.parse_response("{}",1,"apply").error.code == "COMMAND_FAILED","nonzero exit cannot succeed")
	check(Backend.parse_response(JSON.stringify(Backend.fixture("error")),1,"apply").error.code == "STEAM_BUSY","structured nonzero error survives")
	var malformed: Dictionary = original.games[0].duplicate(true)
	malformed.mods = [null]
	check(Backend.parse_response(JSON.stringify(malformed),0,"game").has("error"),"nested malformed mod rejected")
	check(Backend.parse_response('{"version":"1","pending":[],"proton":[null],"steam_running":true}',0,"status").has("error"),"malformed Proton rejected")
	check(Backend.parse_response('{"engine":{},"index":{}}',0,"update").has("error"),"malformed update rejected")
	# Shapes printed by the real engine CLI (src/deckcompanion/cli.py).
	check(not Backend.parse_response('{"engine":{"current":"0.2.0","available":"0.2.0","updated":false},"index":{"updated":false,"fetched_at":null}}',0,"update").has("error"),"engine update before first index fetch accepted")
	check(not Backend.parse_response('{"appid":"2300320","name":"Farming Simulator 25","installed":true,"has_recipe":true,"enabled":false,"pending":"enable","recipe_version":"1.0.0","mods":[{"id":"fs25-deck-mode","name":"FS25 Deck Mode","version":"unresolved","enabled":true,"installed_version":null}],"last_launch":null,"notes":"n","log_tail":[]}',0,"game").has("error"),"engine game detail accepted")
	var game: Dictionary = backend.mock_response("game",["2300320"])
	check(game.mods[0].id == "FS25_DeckMode","FS25 fixture and mod")
	game.name = "changed copy"
	check(backend.games[0].name != game.name,"responses do not alias backend storage")
	check(backend.mock_response("enable",["480"]).error.code == "NO_RECIPE","no recipe cannot enable")
	check(backend.mock_response("enable",["2300320"]).pending == "enable","enable stages pending change")
	check(not backend.mock_response("game",["2300320"]).enabled,"staging does not apply")
	backend.mock_response("mod",["2300320","FS25_DeckMode","--off"])
	check(not backend.games[0].mods[0].enabled,"mod preference saves")
	check(backend.mock_response("status").pending.size() == 2,"pending games counted once")
	check(backend.mock_response("apply",["--restart-steam"]).steam_restarted,"mock apply response")
	check(backend.games[0].enabled and backend.games[0].pending == null,"apply commits staged state")
	check(backend.mock_response("status").pending.is_empty(),"apply clears all pending changes")
	backend.scenario = "offline"
	check(backend.mock_response("games").games.size() == 4,"offline library still usable")
	check(backend.mock_response("update").error.code == "OFFLINE","offline update is actionable")
	backend.scenario = "error"
	backend.mock_response("disable",["2300320"])
	check(backend.mock_response("apply").error.code == "STEAM_BUSY","apply failure reported")
	check(backend.games[0].pending == "disable","failed apply preserves changes")
	backend.scenario = "empty"
	check(backend.mock_response("games").games.is_empty(),"empty library")
	check(Nav.visible_games(original.games,1).size() == 3,"has fix filter")
	check(Nav.visible_games(original.games,2).size() == 2,"enabled filter uses applied state")
	check(Nav.next_index(0,-1,3) == 2 and Nav.next_index(2,1,3) == 0,"focus wraps both directions")
	check(Nav.next_index(0,1,0) == -1,"empty focus list safe")
	var buttons: Array = []
	for i in 3:
		var button := Button.new()
		root.add_child(button)
		buttons.append(button)
	Nav.wire(buttons)
	check(buttons[0].get_node(buttons[0].focus_previous) == buttons[2],"tab order wraps backwards")
	check(buttons[2].get_node(buttons[2].focus_next) == buttons[0],"tab order wraps forwards")
	# D-pad down must go to the control below, not the next one in list order (owner, Deck).
	buttons[0].position = Vector2(0,0); buttons[0].size = Vector2(200,40)
	buttons[1].position = Vector2(300,0); buttons[1].size = Vector2(200,40)
	buttons[2].position = Vector2(0,100); buttons[2].size = Vector2(200,40)
	await process_frame
	check(buttons[0].focus_neighbor_bottom.is_empty(),"no forced down neighbour")
	var unsorted := [{"name":"b game","last_played":null},{"name":"Zeta","last_played":100},{"name":"a game"},{"name":"Alpha","last_played":300}]
	check(Nav.sort_games(unsorted).map(func(g): return g.name) == ["Alpha","Zeta","a game","b game"],"library sorted by last played, then name")
	check(backend.request("games"),"async request accepted")
	check(not backend.request("apply"),"duplicate command rejected while busy")
	await create_timer(0.25).timeout
	check(not backend.busy,"async completion releases busy state")
	var ui: Node = load("res://main.tscn").instantiate()
	root.add_child(ui)
	# Startup reads games, status and persisted splash settings sequentially.
	await create_timer(0.7).timeout
	check(ui.games.size() == 4,"main scene loads fixture library asynchronously")
	await ui_032(ui)
	await ui_034(ui)
	await settings_checks(ui)
	await load("res://tests/layout_polish.gd").new().run(self,ui)
	await load("res://tests/splash_hold.gd").new().run(self)
	await ge_checks(ui)
	await touch_checks(ui)
	# Steam Input's virtual gamepad on the Deck is not device 0.
	for spec in [[JOY_BUTTON_A,"ui_accept"],[JOY_BUTTON_B,"ui_cancel"],[JOY_BUTTON_X,"dc_x"],[JOY_BUTTON_DPAD_DOWN,"ui_down"]]:
		var press := InputEventJoypadButton.new()
		press.device = 1
		press.button_index = spec[0]
		press.pressed = true
		check(press.is_action(spec[1]),"gamepad device 1 button maps to " + spec[1])
	var stick := InputEventJoypadMotion.new()
	stick.device = 2
	stick.axis = JOY_AXIS_LEFT_Y
	stick.axis_value = 1.0
	check(stick.is_action("ui_down"),"left stick on any device navigates")
	var mock_backend = Backend.new()
	mock_backend.mock = true
	check(mock_backend.mock_response("default",["--on"]).pending.any(func(p): return p.get("action") == "default_on"),"default toggle stages a pending change")
	check(Backend.parse_response(JSON.stringify(mock_backend.mock_response("status")),0,"default").has("version"),"default response validates as status")
	mock_backend.mock_response("apply")
	check(mock_backend.mock_response("status").default_tool,"apply commits default tool")
	mock_backend.free()
	check(ui.hints.get_global_rect().end.y <= root.size.y,"library hints fit viewport")
	ui._input(InputEventKey.new())
	var keyboard_event := InputEventKey.new()
	keyboard_event.pressed = true
	ui._input(keyboard_event)
	check(ui.keyboard,"keyboard selects keyboard hints")
	ui._input(InputEventJoypadButton.new())
	check(not ui.keyboard,"gamepad restores drawn glyph hints")
	var tab_event := InputEventJoypadButton.new()
	tab_event.button_index = JOY_BUTTON_RIGHT_SHOULDER
	tab_event.pressed = true
	ui._unhandled_input(tab_event)
	check(ui.tab == 1,"R1 event switches tab")
	ui._switch_tab(0)
	ui._open_game(ui.games[0])
	await create_timer(0.25).timeout
	check(ui.detail and ui.selected.get("notes","") != "","detail loads notes")
	ui._confirm()
	check(ui.dialog.visible and ui.dialog.get_cancel_button().has_focus(),"restart dialog defaults to safe cancel focus")
	ui.dialog.hide()
	ui.modal_focus = ui.log_view
	ui._restore_modal_focus()
	check(ui.log_view.has_focus(),"cancel restores a focused log control")
	ui._toggle_game()
	await create_timer(0.5).timeout
	check(ui.selected.pending == "enable","UI stages toggle")
	ui._request("apply",["--restart-steam"])
	await create_timer(0.7).timeout
	check(ui.selected.pending == null and ui.selected.enabled,"detail refreshes applied state")
	ui._back()
	check(not ui.detail and ui.last_focus == "2300320","back restores game focus identity")
	ui._switch_tab(1)
	check(ui.tab == 1,"tab switches")
	await nonsteam_checks(ui)
	root.size = Vector2i(1920,1080)
	await process_frame
	await process_frame
	check(ui.hints.get_global_rect().end.y <= 1080,"1080p hints fit viewport")
	ui.queue_free()
	for button in buttons: button.queue_free()
	backend.queue_free()
	await process_frame
	print("RESULT: %s/%s checks passed" % [checks-failures,checks])
	quit(0 if failures == 0 else 1)

func nonsteam_capture(name: String) -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--capture-add=") and DisplayServer.get_name() != "headless":
			var directory := arg.trim_prefix("--capture-add=")
			DirAccess.make_dir_recursive_absolute(directory)
			await create_timer(0.2).timeout
			await RenderingServer.frame_post_draw
			check(root.get_texture().get_image().save_png(directory.path_join(name+".png")) == OK,"save add-game capture")

func nonsteam_checks(ui: Node) -> void:
	ui._switch_tab(0)
	await process_frame
	var start := control(ui,"add-game")
	check(start != null,"Games tab offers Add a game")
	if start == null: return
	start.pressed.emit()
	await process_frame
	check(ui.add_flow.folder == "/home/deck","browser starts at Home")
	check(control(ui,"file:Games") != null,"browser exposes controller-focusable folders")
	control(ui,"file:Games").pressed.emit()
	await process_frame
	check(ui.add_flow.folder == "/home/deck/Games","folder activation navigates without FileDialog")
	await nonsteam_capture("add-browser")
	control(ui,"file:Orbit Trails.exe").pressed.emit()
	await process_frame
	check(ui.add_flow.game_name == "Orbit Trails","exe name prefills display name")
	control(ui,"store:gog").pressed.emit()
	control(ui,"add-keyboard").pressed.emit()
	await process_frame
	check(ui.add_flow.store == "gog" and control(ui,"key:A") != null,"store and built-in controller keyboard available")
	control(ui,"key:A").pressed.emit()
	check(ui.add_flow.name_input.text == "Orbit TrailsA","controller key edits name")
	control(ui,"key:erase").pressed.emit()
	check(ui.add_flow.name_input.text == "Orbit Trails","backspace edits name")
	ui.add_flow.set_name("")
	check(control(ui,"add-submit").disabled,"empty name disables Add")
	ui.add_flow.type_key("A")
	check(not control(ui,"add-submit").disabled,"controller typing re-enables Add")
	ui.add_flow.set_name("Orbit Trails")
	ui.add_flow.name_input.text = "Orbit Trails"
	await nonsteam_capture("add-name")
	control(ui,"key:erase").grab_focus()
	await nonsteam_capture("add-keyboard")
	control(ui,"add-submit").pressed.emit()
	await create_timer(0.7).timeout
	var added: Array = ui.games.filter(func(g): return g.get("kind") == "non_steam")
	check(added.size() == 1 and ui.add_flow == null,"add completes and refreshes library")
	if added.is_empty(): return
	var game: Dictionary = added[0]
	check(control(ui,game.appid).find_children("*","Label",true,false).any(func(l): return l.text == "NON-STEAM"),"library displays non-Steam badge")
	check(not ui.status.pending.is_empty(),"add queues Steam apply")
	check(not Backend.parse_response(JSON.stringify(game),0,"add-game").has("error"),"real add response contract accepted")
	ui._open_game(game)
	await create_timer(0.25).timeout
	check(control(ui,"remove-game") != null and control(ui,"toggle") == null,"non-Steam detail has Remove")
	control(ui,"remove-game").pressed.emit()
	check(ui.remove_dialog.visible and ui.remove_dialog.get_cancel_button().has_focus(),"remove confirmation defaults to cancel")
	ui.remove_dialog.hide()
	ui.remove_dialog.confirmed.emit()
	await create_timer(0.7).timeout
	check(not ui.games.any(func(g): return g.get("kind") == "non_steam"),"remove refreshes library")
	check(not ui.status.pending.is_empty(),"removal remains queued until Apply")
	ui._request("apply",["--restart-steam"])
	await create_timer(0.7).timeout
	check(ui.status.pending.is_empty(),"apply clears non-Steam additions and removals")
	control(ui,"add-game").pressed.emit()
	ui._back()
	check(ui.add_flow == null and ui.last_focus == "add-game","B cancels file browser and restores entry focus")
	ui._switch_tab(1)

func control(ui: Node, id: String) -> Control:
	for item in ui.controls:
		if item.get_meta("focus_id", "") == id: return item
	return null

func ui_034(ui: Node) -> void:
	ui._switch_tab(2)
	var toggle: Control = control(ui,"splash_toggle")
	check(toggle != null,"Settings screen has launch splash card")
	if toggle != null:
		check(toggle.button_pressed,"splash defaults on in UI")
		toggle.pressed.emit()
		await create_timer(0.25).timeout
		check(not ui.settings.splash and not control(ui,"splash_toggle").button_pressed,"splash toggle saves and refreshes card")
		control(ui,"splash_more").pressed.emit()
		await create_timer(0.25).timeout
		check(ui.settings.splash_seconds == 4.5 and not ui.settings.splash,"duration saves without losing toggle")
		ui._request("settings")
		await create_timer(0.25).timeout
		check(ui.settings == ui.backend.mock_response("settings"),"settings round-trip via mock backend")
		for seconds in [1,10]:
			ui._request("settings",["--splash-seconds",str(seconds)])
			await create_timer(0.25).timeout
			check(control(ui,"splash_less" if seconds == 1 else "splash_more") == null,"duration stops at bound " + str(seconds))
		check(not ui.notice.contains("Apply when"),"settings apply immediately without Steam restart wording")
	check(Backend.parse_response("{}",0,"settings").has("error"),"settings schema rejects missing fields")
	check(Backend.parse_response('{"splash":true,"splash_seconds":99}',0,"settings").has("error"),"settings schema rejects out-of-range duration")
	var exists := FileAccess.file_exists("res://scripts/splash.gd")
	check(exists,"standalone splash mode exists")
	if exists:
		var splash: Control = load("res://scripts/splash.gd").new()
		splash.auto_quit = false
		splash.file = "res://fixtures/splash.json"
		root.add_child(splash)
		await process_frame
		await process_frame
		check(splash.loaded and splash.title.text == "Farming Simulator 25","splash renders fixture JSON")
		check(not has_backend(splash),"splash contains no engine backend")
		check(splash.proton_line.text.contains("GE-Proton11-11") and splash.fixes_line.text.contains("Community fixes (umu-protonfixes): on"),"splash shows Proton and configured fixes")
		check(splash.mod_rows.get_child_count() == 3 and splash.mod_rows.get_child(1).text.contains("Installed"),"splash shows installed mod state")
		for i in 100:
			await process_frame
			if not splash.cache.worker: break
		check(not splash.art.fallback,"splash artwork decodes on existing background cache")
		check(splash.content.get_global_rect().end.y <= root.size.y,"splash content fits 1280x800")
		if splash.content.get_global_rect().end.y > root.size.y:
			print("Splash content rect: ",splash.content.get_global_rect())
			for child in splash.content.get_children(): print("Child: ",child.get_class()," ",child.size," minimum ",child.get_combined_minimum_size())
		root.size = Vector2i(1920,1080)
		await process_frame
		await process_frame
		check(splash.content.get_global_rect().end.y <= root.size.y,"splash content fits docked 1920x1080")
		root.size = Vector2i(1280,800)
		for spec in [[1,JOY_BUTTON_A],[5,JOY_BUTTON_B]]:
			splash.done = false
			var press := InputEventJoypadButton.new()
			press.device = spec[0]
			press.button_index = spec[1]
			press.pressed = true
			splash._input(press)
			check(splash.done,"splash skips from any device " + str(spec[0]))
		splash.done = false
		var touch := InputEventScreenTouch.new()
		touch.pressed = true
		splash._input(touch)
		touch.pressed = false
		splash._input(touch)
		check(splash.done,"touch skips splash")
		splash.done = false
		splash.elapsed = splash.duration - 0.1
		splash._process(0.2)
		check(splash.done and splash.progress.value == 100,"duration completes progress and finishes splash")
		var fallback: Dictionary = splash.payload.duplicate(true)
		fallback.art = {}
		fallback.warnings = ["Fixture mod blocked: preserving player files"]
		fallback.mods[0].state = "blocked"
		fallback.mods[0].state_message = "Preserving player files"
		splash.set_payload(fallback)
		await process_frame
		check(splash.art.fallback and splash.warnings.visible,"missing artwork uses generated fallback with visible warnings")
		check(splash.mod_rows.get_child(1).text.contains("Blocked"),"blocked mod is visibly labelled")
		splash.queue_free()
		await process_frame
	ui._switch_tab(0)
	ui.notice = ""
	ui._render()
	await process_frame

func has_backend(node: Node) -> bool:
	if node.get_script() == Backend: return true
	for child in node.get_children():
		if has_backend(child): return true
	return false

func settings_checks(ui: Node) -> void:
	check(control(ui,"settings") != null,"third Settings tab exists")
	if control(ui,"settings") == null: return
	for spec in [[JOY_BUTTON_RIGHT_SHOULDER,1],[JOY_BUTTON_RIGHT_SHOULDER,2],[JOY_BUTTON_RIGHT_SHOULDER,0],[JOY_BUTTON_LEFT_SHOULDER,2]]:
		var press := InputEventJoypadButton.new()
		press.device = 4
		press.button_index = spec[0]
		press.pressed = true
		ui._unhandled_input(press)
		check(ui.tab == spec[1],"LB/RB cycles three tabs with correct direction: " + str(spec))
	check(control(ui,"default") != null and control(ui,"splash_toggle") != null,"General contains queued default and splash controls")
	check(ui.update_backend.active == "update" and not ui.backend.busy,"startup update uses independent background backend")
	control(ui,"show_games_without_recipe").pressed.emit()
	await create_timer(0.25).timeout
	check(not ui.settings.show_games_without_recipe,"library preference round-trip")
	ui._switch_tab(0)
	check(control(ui,"480") == null and control(ui,"2300320") != null,"library preference hides only games with no recipe")
	ui._switch_tab(2)
	control(ui,"show_games_without_recipe").pressed.emit()
	await create_timer(0.25).timeout
	ui._switch_tab(0)
	check(control(ui,"480") != null,"library preference restores no-recipe games")
	ui._switch_tab(2)
	ui._show_license(false)
	check(ui.licenses.visible and ui.licenses.get_child(0) is ScrollContainer,"MIT opens scrollable license dialog")
	ui.licenses.hide()
	ui._show_license(true)
	check(ui.licenses.get_child(0).get_child(0).text.contains("Rossen Georgiev") and ui.licenses.get_child(0).get_child(0).text.contains("Godot Engine"),"third-party dialog contains bundled and Godot notices")
	ui.licenses.hide()
	ui._switch_tab(1)
	check(control(ui,"default") == null and control(ui,"splash_toggle") == null,"Updates keeps status and its own update check, no settings toggles")
	var other: Node = load("res://main.tscn").instantiate()
	root.add_child(other)
	other.backend.settings.check_updates_on_start = false
	await create_timer(0.7).timeout
	check(other.startup_settings_read and other.update_backend.active.is_empty(),"disabled startup update makes no update request")
	other.queue_free()
	await process_frame
	ui._switch_tab(0)

func send_mouse(point: Vector2, pressed: bool) -> void:
	var event := InputEventMouseButton.new()
	event.position = point
	event.global_position = point
	event.button_index = MOUSE_BUTTON_LEFT
	event.pressed = pressed
	Input.parse_input_event(event)
	await process_frame

func move_mouse(point: Vector2, relative: Vector2) -> void:
	var event := InputEventMouseMotion.new()
	event.position = point
	event.global_position = point
	event.relative = relative
	event.button_mask = MOUSE_BUTTON_MASK_LEFT
	Input.parse_input_event(event)
	await process_frame

func touch_checks(ui: Node) -> void:
	var exists := FileAccess.file_exists("res://scripts/drag_scroll.gd")
	check(exists,"reusable drag scroller exists")
	var script: Script = load("res://scripts/drag_scroll.gd") if exists else null
	for tab in [0,1,2]:
		ui._switch_tab(tab)
		for area in ui.find_children("*","ScrollContainer",true,false): check(exists and area.get_script() == script,"tab scroll area uses shared drag helper")
	ui._open_game(ui.games[0])
	await create_timer(0.25).timeout
	for area in ui.find_children("*","ScrollContainer",true,false): check(exists and area.get_script() == script,"detail and nested log use shared drag helper")
	ui._back()
	ui._switch_tab(0)
	var scroll: ScrollContainer = script.new() if exists else ScrollContainer.new()
	scroll.position = Vector2(20,20)
	scroll.size = Vector2(320,160)
	root.add_child(scroll)
	var list := VBoxContainer.new()
	list.custom_minimum_size.x = 280
	scroll.add_child(list)
	var taps := [0]
	for i in 14:
		var button := Button.new()
		button.text = "Touch target " + str(i)
		button.custom_minimum_size.y = 64
		button.pressed.connect(func(): taps[0] += 1)
		list.add_child(button)
	await process_frame
	await process_frame
	var first: Button = list.get_child(0)
	var start := first.get_global_rect().get_center()
	await send_mouse(start,true)
	await move_mouse(start+Vector2(0,5),Vector2(0,5))
	await send_mouse(start+Vector2(0,5),false)
	check(taps[0] == 1 and scroll.scroll_vertical == 0,"tap below threshold still activates button")
	await send_mouse(start,true)
	await move_mouse(start-Vector2(0,80),Vector2(0,-80))
	check(scroll.scroll_vertical > 0 and first.disabled,"mouse swipe scrolls and cancels pending button press")
	await send_mouse(start-Vector2(0,80),false)
	await process_frame
	check(taps[0] == 1 and not first.disabled,"swipe release does not click and restores button")
	var before := scroll.scroll_vertical
	if exists: scroll._process(0.04)
	check(scroll.scroll_vertical > before,"swipe continues with light inertia")
	var touch := InputEventScreenTouch.new()
	touch.index = 7
	touch.position = Vector2(100,120)
	touch.pressed = true
	Input.parse_input_event(touch)
	await process_frame
	var drag := InputEventScreenDrag.new()
	drag.index = 7
	drag.position = touch.position-Vector2(0,50)
	drag.relative = Vector2(0,-50)
	before = scroll.scroll_vertical
	Input.parse_input_event(drag)
	await process_frame
	check(scroll.scroll_vertical > before,"ScreenTouch/ScreenDrag scroll without touchscreen detection")
	touch.pressed = false
	touch.position = drag.position
	Input.parse_input_event(touch)
	await process_frame
	var key := InputEventKey.new()
	key.pressed = true
	key.physical_keycode = KEY_DOWN
	if exists: scroll._input(key)
	check(exists and scroll.velocity == Vector2.ZERO,"keyboard/controller navigation stops touch inertia")
	var nested: ScrollContainer = script.new() if exists else ScrollContainer.new()
	nested.custom_minimum_size = Vector2(260,100)
	list.add_child(nested)
	list.move_child(nested,0)
	var long_text := Label.new()
	long_text.text = "Nested log"
	long_text.custom_minimum_size.y = 800
	nested.add_child(long_text)
	scroll.scroll_vertical = 0
	await process_frame
	await process_frame
	start = nested.get_global_rect().get_center()
	await send_mouse(start,true)
	await move_mouse(start-Vector2(0,50),Vector2(0,-50))
	check(nested.scroll_vertical > 0 and scroll.scroll_vertical == 0,"nested scroll owns swipe without moving its outer container")
	await send_mouse(start-Vector2(0,50),false)
	scroll.queue_free()
	await process_frame

func ui_032(ui: Node) -> void:
	var modern := false
	for property in ui.get_property_list():
		if property.name == "preview_fields": modern = true
	check(modern,"library exposes a live game preview")
	if modern:
		var first: Control = control(ui,"2300320")
		first.grab_focus()
		await process_frame
		check(ui.preview_fields.name.text == ui.games[0].name,"preview follows first game focus")
		var panel: Node = ui.preview
		var banner: Node = ui.preview_art
		var press := InputEventJoypadButton.new()
		press.device = 1
		press.button_index = JOY_BUTTON_DPAD_DOWN
		press.pressed = true
		Input.parse_input_event(press)
		await process_frame
		await process_frame
		press.pressed = false
		Input.parse_input_event(press)
		var ordered: Array = Nav.sort_games(Nav.visible_games(ui.games,ui.filter),ui.sort_by_name)
		check(ui.preview_fields.name.text == ordered[1].name,"D-pad moves preview to next game without opening detail")
		var up := InputEventAction.new()
		up.action = "ui_up"
		control(ui,ordered[0].appid).grab_focus()
		up.pressed = true
		Input.parse_input_event(up)
		await process_frame
		await process_frame
		up.pressed = false
		Input.parse_input_event(up)
		var owner: Control = root.gui_get_focus_owner()
		check(owner != null and owner.get_meta("focus_id","") == "filter%s" % ui.filter,"up from the first game lands on the active filter, not a top-bar button")
		check(ui.preview == panel and ui.preview_art == banner and not ui.detail,"focus changes retain panel and artwork nodes without flicker")
		control(ui,"480").grab_focus()
		await process_frame
		check(ui.preview_art.fallback and ui.preview_art.texture.texture == null,"null art uses generated fallback and clears previous image")
		check(ui.preview_fields.fix.text == "No recipe — Steam launches it normally","preview explains no-recipe behavior")
		control(ui,"2300320").grab_focus()
		for i in 100:
			await process_frame
			if not ui.art_cache.worker: break
		check(not ui.preview_art.fallback,"local fixture artwork completes asynchronously")
		check(ui.preview_fields.proton.text.contains("GE-Proton11-7-x86_64") and ui.preview_fields.proton.text.contains("fallback"),"preview shows selected Proton and reason")
		check(ui.preview_fields.launch.text.contains("2026-10-31") and ui.preview_fields.launch.text.contains("handoff"),"preview shows last launch handoff and time honestly")
		check(ui.preview_fields.note.text == ui.games[0].notes.get_slice("\n",0),"preview shows first line of notes")
		# Complete an obsolete image request after selecting another game.
		var old_key: String = ui.preview_art.key
		var old_texture: Texture2D = ui.preview_art.texture.texture
		control(ui,"480").grab_focus()
		ui.art_cache.loaded.emit(old_key,old_texture)
		check(ui.preview_art.fallback and ui.preview_art.texture.texture == null,"late artwork cannot replace the focused game's fallback")
		# Missing hero falls through to header, then portrait on the worker.
		var fixture_path: String = ui.games[0].art.hero
		for kind in ["header","portrait"]:
			var candidate := {"appid":"test-"+kind,"art":{"hero":"res://missing-art.png",kind:fixture_path}}
			ui.preview_art.show_game(candidate)
			for i in 100:
				await process_frame
				if not ui.art_cache.worker: break
			check(not ui.preview_art.fallback,"art loads " + kind + " when preferred hero is missing")
		for i in 10:
			var candidates: Array[String] = ["res://missing-%s.png" % i]
			ui.art_cache.request(candidates)
			while ui.art_cache.worker: await process_frame
		check(ui.art_cache.textures.size() <= 8,"art cache stays bounded including failed paths")
		var without_notes: Dictionary = ui.games[0].duplicate(true)
		without_notes.erase("notes")
		var saved_notes: Array = ui.games.duplicate(true)
		ui.games[0] = without_notes
		ui._render()
		control(ui,"2300320").grab_focus()
		check(ui.preview_backend.busy and not ui.backend.busy and not control(ui,"480").disabled,"missing list notes load on an independent worker without blocking input")
		control(ui,"480").grab_focus()
		await create_timer(0.25).timeout
		check(ui.preview_fields.name.text == ui.games[2].name and ui.preview_fields.note.text == ui.games[2].notes.get_slice("\n",0),"late recipe notes cannot replace a different focused game")
		control(ui,"2300320").grab_focus()
		check(ui.preview_fields.note.text == saved_notes[0].notes.get_slice("\n",0),"old-engine list without notes uses cached game detail")
		ui.games = saved_notes
	# Exercise real detail buttons, not only the formatting helper.
	ui._open_game(ui.games[0])
	await create_timer(0.25).timeout
	var original: Dictionary = ui.selected.duplicate(true)
	var mod: Dictionary = original.mods[0].duplicate(true)
	mod.installed_version = "0.7.0"
	for state in ["installed","will_install_on_launch","update_available","off","blocked"]:
		mod.state = state
		mod.enabled = state != "off"
		mod.state_message = "Game is running"
		ui.selected.mods = [mod.duplicate(true)]
		ui._render()
		var row: Button = control(ui,"mod"+mod.id)
		var expected: String = {"installed":"Installed v0.7.0","will_install_on_launch":"Will install on next launch","update_available":"Update available","off":"Off","blocked":"Blocked: Game is running"}[state]
		check(row.tooltip_text.begins_with("[On]" if mod.enabled else "[Off]") and row.tooltip_text.contains(expected) and row.button_pressed == mod.enabled and not row.text.contains("✓"),"mod row separates preference and " + state)
	# Legacy objects lack all added fields and still render in both screens.
	var legacy: Dictionary = original.duplicate(true)
	legacy.erase("art")
	legacy.erase("proton")
	legacy.mods[0].erase("state")
	legacy.mods[0].erase("state_message")
	for installed in [null,"0.7.0","0.6.0"]:
		legacy.mods[0].installed_version = installed
		ui.selected = legacy
		ui._render()
		var row: Button = control(ui,"mod"+mod.id)
		check(row.tooltip_text.contains("Will install on next launch" if installed == null else ("Installed v0.7.0" if installed == "0.7.0" else "Update available")),"legacy mod state from installed_version " + str(installed))
	check(not Backend.parse_response(JSON.stringify(legacy),0,"game").has("error"),"legacy game object remains accepted")
	legacy.mods[0].version = "unresolved"
	legacy.mods[0].installed_version = "0.7.0"
	ui.selected = legacy
	ui._render()
	check(control(ui,"mod"+mod.id).tooltip_text.contains("Installed v0.7.0") and not control(ui,"mod"+mod.id).tooltip_text.contains("Update available"),"legacy unresolved version does not invent a mod update")
	ui.games[0] = legacy
	ui._back()
	await process_frame
	if modern:
		check(ui.preview_art.fallback and ui.preview_fields.proton.text.contains("Not reported"),"legacy library renders missing art and Proton safely")
		legacy.art = null
		legacy.proton = null
		ui.preview_id = ""
		ui._focus_game(legacy)
		check(ui.preview_art.fallback and ui.preview_fields.proton.text.contains("Not reported"),"explicit null artwork and Proton render safely")
		for value in ["enable","disable",null]:
			legacy.pending = value
			ui.preview_id = ""
			ui._focus_game(legacy)
			check(ui.preview_fields.fix.text == ("Pending "+value if value != null else "Fix available"),"preview fix state " + str(value))
	# A mod mutation alone must not introduce a pending Steam change.
	var backend := Backend.new()
	root.add_child(backend)
	backend.mock = true
	var before: Array = backend.mock_response("status").pending
	var toggled: Dictionary = backend.mock_response("mod",["2300320","FS25_DeckMode","--off"])
	check(backend.mock_response("status").pending == before,"mod preference never creates pending Steam changes")
	check(toggled.get("mod_result") is Dictionary and not toggled.get("mod_result",{}).get("message", "").is_empty(),"mock mod mutation returns player-facing mod_result")
	ui.detail = true
	for message in ["Deck Mode installed now.",null]:
		var response: Dictionary = original.duplicate(true)
		if message != null: response.mod_result = {"changed_now":true,"message":message}
		ui._completed("mod",response)
		await create_timer(0.25).timeout
		check(ui.notice == (message if message != null else "Saved. Takes effect on next launch."),"mod toggle chooses engine message or legacy fallback " + str(message))
	ui._completed("enable",original.duplicate(true))
	await create_timer(0.25).timeout
	check(ui.notice.contains("Apply when"),"enable keeps Steam Apply wording")
	ui._completed("disable",original.duplicate(true))
	await create_timer(0.25).timeout
	check(ui.notice.contains("Apply when"),"disable keeps Steam Apply wording")
	ui._completed("default",ui.status.duplicate(true))
	check(ui.notice.contains("Apply when"),"default keeps Steam Apply wording")
	# Restore baseline state for the pre-existing UI checks.
	ui.games = Backend.fixture("games").games
	ui.selected = original
	ui.detail = false
	ui.notice = ""
	ui.last_focus = "games"
	ui._render()
	await process_frame
	backend.queue_free()

func ge_checks(ui: Node) -> void:
	ui._switch_tab(2)
	check(control(ui,"auto_install_ge") != null and control(ui,"cleanup_old_ge") != null,"GE settings toggles exist")
	if control(ui,"auto_install_ge") == null: return
	control(ui,"auto_install_ge").pressed.emit()
	await create_timer(0.25).timeout
	check(not ui.settings.auto_install_ge,"GE auto-install setting saves through existing settings command")
	control(ui,"cleanup_old_ge").pressed.emit()
	await create_timer(0.25).timeout
	check(not ui.settings.cleanup_old_ge,"GE cleanup setting saves independently")
	check(Backend.parse_response('{"active":false,"builds":[null]}',0,"proton").has("error"),"malformed build rejected")
	check(not Backend.parse_response('{"active":true,"scheduled":true}',0,"update").has("error"),"detached update acknowledgement accepted")
	ui._switch_tab(1)
	await create_timer(0.3).timeout
	var install := control(ui,"proton_GE-Proton11-11")
	check(install != null,"wanted build has Install action")
	if install == null: return
	install.pressed.emit()
	await create_timer(0.25).timeout
	check(ui.proton_data.active and not ui.backend.busy,"install scheduled independently of library backend")
	ui.proton_backend.request("proton")
	await create_timer(0.25).timeout
	check(ui.proton_data.builds[0].state == "downloading" and ui.proton_data.builds[0].percent >= 42,"poll exposes download percentage")
	check(ui.body.find_children("*","Button",true,false).any(func(b): return b.get_meta("focus_id","") == "proton_GE-Proton11-11" and b.disabled),"active download blocks duplicate install")
	ui.proton_backend.request("proton")
	await create_timer(0.25).timeout
	ui.proton_backend.request("proton")
	await create_timer(0.25).timeout
	check(ui.proton_data.builds[0].state == "installed" and not ui.proton_data.active,"poll reaches installed state")
	check(control(ui,"proton_GE-Proton11-11") == null,"installed build no longer offers install")
	ui.proton_backend.scenario = "download_failed"
	ui._install_proton("GE-Proton11-11")
	await create_timer(0.25).timeout
	ui.proton_backend.request("proton")
	await create_timer(0.25).timeout
	check(ui.proton_data.builds[0].state == "failed" and ui.proton_data.builds[0].error.contains("disk"),"poll preserves actionable failure reason")
	check(control(ui,"proton_GE-Proton11-11") != null,"failed build offers retry")
	ui.proton_backend.scenario = ""
	ui._switch_tab(0)
