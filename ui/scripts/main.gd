extends Control
const Backend = preload("res://scripts/backend.gd")
const Nav = preload("res://scripts/navigation.gd")
const Landscape = preload("res://scripts/landscape.gd")
const Hints = preload("res://scripts/hints.gd")
const INK := Color("e7f1f4")
const MUTED := Color("9ab0bc")
const MINT := Color("b8f18b")
var backend: Node
var root: VBoxContainer
var body: VBoxContainer
var hints: Control
var pending_label: Label
var apply_button: Button
var controls: Array = []
var games: Array = []
var status: Dictionary = {}
var update: Dictionary = {}
var selected: Dictionary = {}
var tab := 0
var filter := 0
var detail := false
var keyboard := false
var notice := ""
var error := ""
var failed_command := "games"
var failed_args: Array = []
var log_view: ScrollContainer
var dialog: ConfirmationDialog
var last_focus := ""
var modal_focus: Control

func _ready() -> void:
	_setup_actions()
	_make_theme()
	backend = Backend.new()
	add_child(backend)
	backend.completed.connect(_completed)
	_build_shell()
	_refresh()
	_capture_if_requested()

func _setup_actions() -> void:
	for spec in [["dc_x", KEY_X, JOY_BUTTON_X],["dc_y", KEY_Y, JOY_BUTTON_Y],["dc_left", KEY_Q, JOY_BUTTON_LEFT_SHOULDER],["dc_right", KEY_E, JOY_BUTTON_RIGHT_SHOULDER]]:
		InputMap.add_action(spec[0])
		var key := InputEventKey.new()
		key.physical_keycode = spec[1]
		InputMap.action_add_event(spec[0],key)
		var joy := InputEventJoypadButton.new()
		joy.button_index = spec[2]
		InputMap.action_add_event(spec[0],joy)

func _style(bg: Color, border: Color = Color.TRANSPARENT) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = bg
	box.border_color = border
	box.set_border_width_all(2 if border.a else 0)
	box.set_corner_radius_all(12)
	box.content_margin_left = 20
	box.content_margin_right = 20
	box.content_margin_top = 12
	box.content_margin_bottom = 12
	return box

func _make_theme() -> void:
	theme = Theme.new()
	theme.default_font_size = 20
	theme.set_color("font_color","Label",INK)
	for state in ["normal","hover","pressed","disabled"]:
		theme.set_stylebox(state,"Button",_style(Color("1c303c") if state != "hover" else Color("2b4652")))
		theme.set_color("font_color" + ("" if state == "normal" else "_"+state),"Button",INK if state != "disabled" else Color("637b88"))
	theme.set_stylebox("focus","Button",_style(Color(0.65,0.94,0.55,0.08),MINT))
	theme.set_stylebox("panel","AcceptDialog",_style(Color("122530"),Color("638279")))
	theme.set_constant("separation","VBoxContainer",12)
	theme.set_constant("separation","HBoxContainer",12)
	theme.set_color("font_color","CheckButton",INK)

func _label(text: String, size_px := 20, color := INK) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size",size_px)
	label.add_theme_color_override("font_color",color)
	return label

func _button(text: String, action: Callable, parent: Node, id: String, enabled := true) -> Button:
	var button := Button.new()
	button.text = text
	button.custom_minimum_size.y = 48
	button.disabled = not enabled or backend.busy
	button.set_meta("focus_id",id)
	button.pressed.connect(action)
	button.focus_entered.connect(func(): last_focus = id)
	parent.add_child(button)
	if not button.disabled: controls.append(button)
	return button

func _row(parent: Node) -> HBoxContainer:
	var row := HBoxContainer.new()
	parent.add_child(row)
	return row

func _card(parent: Node) -> VBoxContainer:
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel",_style(Color("12232e")))
	parent.add_child(panel)
	var column := VBoxContainer.new()
	panel.add_child(column)
	return column

func _paragraph(parent: Node, text: String, color := MUTED) -> void:
	var label := _label(text,19,color)
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	parent.add_child(label)

func _build_shell() -> void:
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left","right","top","bottom"]: margin.add_theme_constant_override("margin_"+side,32 if side != "bottom" else 16)
	add_child(margin)
	root = VBoxContainer.new()
	margin.add_child(root)
	var brand := _row(root)
	brand.add_child(_label("◈  DECKCOMPANION",20,MINT))
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	brand.add_child(spacer)
	brand.add_child(_label("LOCAL PREVIEW  /  MOCK DATA" if backend.mock else "YOUR DECK. YOUR GAMES.",15,MUTED))
	body = VBoxContainer.new()
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(body)
	var pending := PanelContainer.new()
	pending.add_theme_stylebox_override("panel",_style(Color("23382e")))
	root.add_child(pending)
	var row := _row(pending)
	pending_label = _label("")
	pending_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(pending_label)
	apply_button = _button("Apply and restart Steam",_confirm,row,"apply")
	hints = Hints.new()
	hints.custom_minimum_size.y = 44
	root.add_child(hints)
	dialog = ConfirmationDialog.new()
	dialog.title = "Apply changes and restart Steam?"
	dialog.dialog_text = "Steam will close and restart to apply your changes.\nThis management UI will also close when Steam restarts.\nFinish any running game and save your progress first."
	dialog.ok_button_text = "Apply and restart"
	dialog.cancel_button_text = "Keep editing"
	dialog.min_size = Vector2i(660,240)
	dialog.confirmed.connect(func(): _request("apply",["--restart-steam"]))
	dialog.canceled.connect(_restore_modal_focus)
	add_child(dialog)

func _restore_modal_focus() -> void:
	if is_instance_valid(modal_focus) and modal_focus.is_visible_in_tree(): modal_focus.grab_focus()

func _confirm() -> void:
	if backend.busy or status.get("pending",[]).is_empty(): return
	modal_focus = get_viewport().gui_get_focus_owner()
	dialog.popup_centered()
	dialog.get_cancel_button().grab_focus()

func _request(command: String, args: Array = []) -> void:
	failed_command = command
	failed_args = args.duplicate()
	backend.request(command,args)
	_render()

func _refresh() -> void:
	_request("games")

func _completed(command: String, data: Dictionary) -> void:
	if data.has("error"):
		error = data.error.code + "  ·  " + data.error.message
		notice = ""
	else:
		error = ""
		match command:
			"games":
				games = data.games
				if detail:
					for game in games:
						if game.appid == selected.get("appid"): selected.merge(game,true)
				_request("status")
				return
			"status": status = data
			"game": selected = data
			"enable", "disable", "mod":
				selected.merge(data,true)
				for i in games.size():
					if games[i].appid == data.appid: games[i] = data
				notice = "Change saved. Apply when you’re ready."
				_request("status")
				return
			"update":
				update = data
				notice = "Update check complete."
				_request("status")
				return
			"apply":
				notice = "Changes applied. Mock Steam restart completed." if backend.mock else "Changes applied. Steam is restarting."
				_refresh()
				return
	_render()

func _render() -> void:
	for node in body.get_children():
		body.remove_child(node)
		node.queue_free()
	controls.clear()
	log_view = null
	var heading := _row(body)
	var title := _label(("Game details" if detail and not selected.is_empty() else "Your library") if tab == 0 else "System & updates",38)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(title)
	_button("Games" + ("  •" if tab == 0 else ""),func(): _switch_tab(0),heading,"games")
	_button("Updates" + ("  •" if tab == 1 else ""),func(): _switch_tab(1),heading,"updates")
	if backend.busy: body.add_child(_label("Working…  Your changes are being read safely.",18,MINT))
	elif not error.is_empty():
		var alert := _card(body)
		_paragraph(alert,error,Color("ffbd9d"))
		_button("Try again",func(): _request(failed_command,failed_args),alert,"retry")
	elif not notice.is_empty(): body.add_child(_label(notice,18,MINT))
	if tab == 1: _updates_screen()
	elif detail and not selected.is_empty(): _detail_screen()
	else: _games_screen()
	var count: int = status.get("pending",[]).size()
	pending_label.text = "%s game%s with pending changes" % [count,"" if count == 1 else "s"] if count else "Everything is up to date  ·  No pending changes"
	apply_button.disabled = count == 0 or backend.busy
	if not apply_button.disabled: controls.append(apply_button)
	hints.keyboard = keyboard
	hints.detail = detail
	hints.queue_redraw()
	Nav.wire(controls)
	_restore_focus.call_deferred()

func _restore_focus() -> void:
	if not controls.is_empty():
		var target: Control = controls[0]
		for control in controls:
			if control.get_meta("focus_id","") == last_focus: target = control
		if is_instance_valid(target) and target.is_inside_tree(): target.grab_focus()

func _games_screen() -> void:
	var filters := _row(body)
	for i in 3:
		_button((["All games","Has fix","Enabled"][i]) + ("  ✓" if filter == i else ""),_set_filter.bind(i),filters,"filter%s" % i)
	var visible := Nav.visible_games(games,filter)
	var split := _row(body)
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var scroll := ScrollContainer.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_stretch_ratio = 1.65
	scroll.follow_focus = true
	split.add_child(scroll)
	var list := VBoxContainer.new()
	list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(list)
	if visible.is_empty():
		_paragraph(list,"No games match this filter." if not games.is_empty() else "Your library is waiting. Install a Steam game, then refresh.")
		_button("Refresh library",_refresh,list,"refresh")
	for game in visible:
		var state := "Pending " + str(game.pending) if game.get("pending") != null else ("Enabled" if game.enabled else "Not enabled")
		if game.get("pending") == null and status.get("pending",[]).any(func(p): return p is Dictionary and p.get("appid") == game.appid): state += " · Pending mods"
		var launch: Variant = game.get("last_launch")
		var result := "Not launched" if launch == null else ("Last launch OK" if launch.get("ok",false) else "Last launch failed")
		var text: String = game.name + "\n" + ("Recipe " + str(game.get("recipe_version","")) if game.has_recipe else "No recipe available") + "   ·   " + state + "   ·   " + result
		var button := _button(text,_open_game.bind(game),list,game.appid)
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		button.add_theme_font_size_override("font_size",18)
		button.custom_minimum_size.y = 92
	var feature := _card(split)
	feature.get_parent().size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var art := Landscape.new()
	art.custom_minimum_size.y = 150
	art.mouse_filter = Control.MOUSE_FILTER_IGNORE
	feature.add_child(art)
	feature.add_child(_label("MADE FOR THE HANDHELD",14,MINT))
	feature.add_child(_label("Less setup.\nMore play.",32))
	_paragraph(feature,"Browse community recipes, choose your mods, and get back to your games.")

func _open_game(game: Dictionary) -> void:
	selected = game
	detail = true
	notice = ""
	last_focus = "toggle"
	_request("game",[game.appid])

func _detail_screen() -> void:
	var row := _row(body)
	_button("‹  Library",_back,row,"back")
	var name_label := _label(selected.name,28)
	name_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(name_label)
	var enabled: bool = selected.get("pending") == "enable" or (selected.get("pending") == null and selected.enabled)
	_button("Disable" if enabled else "Enable DeckCompanion",_toggle_game,row,"toggle",selected.has_recipe)
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	body.add_child(scroll)
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(column)
	var mods := _card(column)
	mods.add_child(_label("Mods & recipe",23))
	if selected.mods.is_empty(): _paragraph(mods,"No optional mods in this recipe." if selected.has_recipe else "A recipe isn’t available yet. Steam will launch this game normally.")
	for mod in selected.mods:
		var version: String = "latest" if mod.version == "unresolved" else "v" + mod.version
		var installed: String = "Not installed yet" if mod.get("installed_version") == null else "Installed v" + str(mod.installed_version)
		var state: String = ("On  ·  " if mod.enabled else "Off  ·  ") + installed
		var mod_button := _button(("✓   " if mod.enabled else "○   ") + mod.name + "   " + version + "      " + state,_toggle_mod.bind(mod),mods,"mod"+mod.id)
		mod_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	var notes := _card(column)
	notes.add_child(_label("Recipe notes",23))
	_paragraph(notes,str(selected.get("notes","Loading recipe notes…")))
	var logs := _card(column)
	logs.add_child(_label("Last launch log",23))
	log_view = ScrollContainer.new()
	log_view.custom_minimum_size.y = 125
	log_view.focus_mode = Control.FOCUS_ALL
	log_view.add_theme_stylebox_override("focus",_style(Color.TRANSPARENT,MINT))
	logs.add_child(log_view)
	controls.append(log_view)
	var log_text := _label("\n".join(selected.get("log_tail",[])) if not selected.get("log_tail",[]).is_empty() else "No launch log yet.",17,MUTED)
	log_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	log_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	log_view.add_child(log_text)

func _updates_screen() -> void:
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	body.add_child(scroll)
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(column)
	var engine := _card(column)
	engine.add_child(_label("Engine  /  " + str(status.get("version","Not connected")),26))
	var available: String = str(update.get("engine",{}).get("available","Not checked"))
	_paragraph(engine,"Available version: " + available + "   ·   Steam " + ("is running" if status.get("steam_running",false) else "is not running"))
	_button("Check for updates",func(): _request("update"),engine,"check")
	var index := _card(column)
	index.add_child(_label("Recipe index",23))
	var raw_fetched = update.get("index",{}).get("fetched_at")
	var fetched: String = raw_fetched if raw_fetched is String else ""
	var age := "Not fetched yet. Check for updates to download the recipe index."
	if not fetched.is_empty():
		var hours := maxi(0,int((Time.get_unix_time_from_system()-Time.get_unix_time_from_datetime_string(fetched))/3600))
		age = ("Fetched just now" if hours == 0 else "Fetched %s hour%s ago" % [hours,"" if hours == 1 else "s"]) + "  ·  " + fetched
	_paragraph(index,age)
	var proton := _card(column)
	proton.add_child(_label("Installed Proton builds",23))
	for build in status.get("proton",[]): _paragraph(proton,str(build.get("name","Unknown build")) + "\n" + str(build.get("path","")))
	if status.get("proton",[]).is_empty(): _paragraph(proton,"No Proton builds found. Install one through Steam to get started.")
	_button("Refresh system status",func(): _request("status"),proton,"status")

func _toggle_game() -> void:
	if not detail or not selected.get("has_recipe",false) or backend.busy: return
	var enabled: bool = selected.get("pending") == "enable" or (selected.get("pending") == null and selected.enabled)
	_request("disable" if enabled else "enable",[selected.appid])

func _toggle_mod(mod: Dictionary) -> void:
	_request("mod",[selected.appid,mod.id,"--off" if mod.enabled else "--on"])

func _set_filter(value: int) -> void:
	filter = value
	_render()

func _switch_tab(value: int) -> void:
	if backend.busy: return
	tab = value
	detail = false
	last_focus = "updates" if value == 1 else "games"
	_render()

func _back() -> void:
	if backend.busy: return
	if detail:
		detail = false
		last_focus = selected.get("appid","")
	elif tab == 1: tab = 0
	_render()

func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed: keyboard = true
	elif event is InputEventJoypadButton or (event is InputEventJoypadMotion and absf(event.axis_value) > 0.3): keyboard = false
	if hints:
		hints.keyboard = keyboard
		hints.queue_redraw()

func _unhandled_input(event: InputEvent) -> void:
	if dialog.visible or backend.busy: return
	if event.is_action_pressed("ui_cancel"): _back()
	elif event.is_action_pressed("dc_left") or event.is_action_pressed("dc_right"): _switch_tab(Nav.next_index(tab,1,2))
	elif event.is_action_pressed("dc_x"):
		if detail: _toggle_game()
		elif tab == 0: _set_filter(Nav.next_index(filter,1,3))
	elif event.is_action_pressed("dc_y"): _confirm()
	else: return
	get_viewport().set_input_as_handled()

func _process(delta: float) -> void:
	if is_instance_valid(log_view) and not dialog.visible:
		var axis := Input.get_joy_axis(0,JOY_AXIS_RIGHT_Y)
		if Input.is_physical_key_pressed(KEY_PAGEDOWN): axis = 1.0
		if Input.is_physical_key_pressed(KEY_PAGEUP): axis = -1.0
		if absf(axis) > 0.25: log_view.scroll_vertical += int(axis*420*delta)

func _capture_if_requested() -> void:
	var directory := ""
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--capture-dir="): directory = arg.trim_prefix("--capture-dir=")
	if directory.is_empty() or not backend.mock: return
	DirAccess.make_dir_recursive_absolute(directory)
	for screen in ["games","detail","updates"]:
		if screen == "detail": _open_game(games[0])
		if screen == "updates":
			_switch_tab(1)
			_request("update")
		await get_tree().create_timer(1.0).timeout
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(directory.path_join(screen+".png"))
	get_tree().quit()
