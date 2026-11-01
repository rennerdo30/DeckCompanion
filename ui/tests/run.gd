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
	var game: Dictionary = backend.mock_response("game",["2300320"])
	check(game.mods[0].id == "FS25_DeckMode","FS25 fixture and mod")
	game.name = "changed copy"
	check(backend.games[0].name != game.name,"responses do not alias backend storage")
	check(backend.mock_response("enable",["480"]).error.code == "NO_RECIPE","no recipe cannot enable")
	check(backend.mock_response("enable",["2300320"]).pending == "enable","enable stages pending change")
	check(not backend.mock_response("game",["2300320"]).enabled,"staging does not apply")
	backend.mock_response("mod",["2300320","FS25_DeckMode","--off"])
	check(not backend.games[0].mods[0].enabled,"mod toggle stages")
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
	check(buttons[0].get_node(buttons[0].focus_neighbor_top) == buttons[2],"actual focus path wraps up")
	check(buttons[2].get_node(buttons[2].focus_neighbor_bottom) == buttons[0],"actual focus path wraps down")
	check(backend.request("games"),"async request accepted")
	check(not backend.request("apply"),"duplicate command rejected while busy")
	await create_timer(0.25).timeout
	check(not backend.busy,"async completion releases busy state")
	var ui: Node = load("res://main.tscn").instantiate()
	root.add_child(ui)
	await create_timer(0.5).timeout
	check(ui.games.size() == 4,"main scene loads fixture library asynchronously")
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
