extends Control
const Backend = preload("res://scripts/backend.gd")
const Nav = preload("res://scripts/navigation.gd")
const Hints = preload("res://scripts/hints.gd")
const ArtCache = preload("res://scripts/art_cache.gd")
const GameArt = preload("res://scripts/game_art.gd")
const Text = preload("res://scripts/game_text.gd")
const DragScroll = preload("res://scripts/drag_scroll.gd")
const UI_VERSION := "0.3.4"
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
var settings: Dictionary = {}
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
var art_cache: Node
var preview: VBoxContainer
var preview_art: Control
var preview_fields: Dictionary = {}
var preview_id := ""
var focused_game := ""
var preview_scroll: ScrollContainer
var preview_backend: Node
var preview_note_id := ""
var preview_notes: Dictionary = {}
var update_backend: Node
var startup_settings_read := false
var licenses: AcceptDialog
var screen_scroll: ScrollContainer
var license_scroll: ScrollContainer

func _ready() -> void:
	_setup_actions()
	_make_theme()
	art_cache = ArtCache.new()
	add_child(art_cache)
	backend = Backend.new()
	add_child(backend)
	backend.completed.connect(_completed)
	update_backend = Backend.new()
	add_child(update_backend)
	update_backend.completed.connect(_update_completed)
	# The list contract does not include notes on older engines. Read detail separately
	# without locking navigation or rebuilding the panel while focus moves.
	preview_backend = Backend.new()
	add_child(preview_backend)
	preview_backend.completed.connect(_preview_completed)
	_build_shell()
	_refresh()
	_capture_if_requested()

const ALL_DEVICES := -1

func _setup_actions() -> void:
	if InputMap.has_action("dc_x"): return
	for spec in [["dc_x", KEY_X, JOY_BUTTON_X],["dc_y", KEY_Y, JOY_BUTTON_Y],["dc_left", KEY_Q, JOY_BUTTON_LEFT_SHOULDER],["dc_right", KEY_E, JOY_BUTTON_RIGHT_SHOULDER]]:
		InputMap.add_action(spec[0])
		var key := InputEventKey.new()
		key.physical_keycode = spec[1]
		InputMap.action_add_event(spec[0],key)
		InputMap.action_add_event(spec[0],_joy_button(spec[2]))
	# On the Steam Deck, Steam Input exposes a virtual gamepad that is not device 0,
	# so every controller binding must match all devices (hardware check 2026-11-01).
	for spec in [["ui_accept", JOY_BUTTON_A],["ui_cancel", JOY_BUTTON_B],["ui_up", JOY_BUTTON_DPAD_UP],["ui_down", JOY_BUTTON_DPAD_DOWN],["ui_left", JOY_BUTTON_DPAD_LEFT],["ui_right", JOY_BUTTON_DPAD_RIGHT]]:
		InputMap.action_add_event(spec[0],_joy_button(spec[1]))
	for spec in [["ui_up", JOY_AXIS_LEFT_Y, -1.0],["ui_down", JOY_AXIS_LEFT_Y, 1.0],["ui_left", JOY_AXIS_LEFT_X, -1.0],["ui_right", JOY_AXIS_LEFT_X, 1.0]]:
		var motion := InputEventJoypadMotion.new()
		motion.device = ALL_DEVICES
		motion.axis = spec[1]
		motion.axis_value = spec[2]
		InputMap.action_add_event(spec[0],motion)

static func _joy_button(button: JoyButton) -> InputEventJoypadButton:
	var joy := InputEventJoypadButton.new()
	joy.device = ALL_DEVICES
	joy.button_index = button
	return joy

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
	licenses = AcceptDialog.new()
	licenses.title = "Licenses"
	licenses.min_size = Vector2i(760,520)
	licenses.confirmed.connect(_restore_modal_focus)
	licenses.canceled.connect(_restore_modal_focus)
	add_child(licenses)

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
				preview_notes.clear()
				if detail:
					for game in games:
						if game.appid == selected.get("appid"): selected.merge(game,true)
				_request("status")
				return
			"status":
				status = data
				if settings.is_empty():
					_request("settings")
					return
			"settings":
				settings = data
				if not failed_args.is_empty(): notice = "Settings saved. Takes effect immediately."
				if not startup_settings_read:
					startup_settings_read = true
					if settings.get("check_updates_on_start",true): _check_updates()
			"default":
				status = data
				notice = "Change saved. Apply when you’re ready."
			"game": selected = data
			"enable", "disable", "mod":
				selected.merge(data,true)
				for i in games.size():
					if games[i].appid == data.appid: games[i] = data
				notice = Text.mod_message(data) if command == "mod" else "Change saved. Apply when you’re ready."
				_request("status")
				return
			"update":
				update = data
				notice = "Update check complete."
				_request("status")
				return
			"apply":
				if data.get("scheduled", false): notice = "Steam is restarting now to apply your changes…"
				else: notice = "Changes applied. Mock Steam restart completed." if backend.mock else "Changes applied. Steam is restarting."
				_refresh()
				return
	_render()

func _render() -> void:
	screen_scroll = null
	preview = null
	preview_scroll = null
	preview_fields.clear()
	preview_id = ""
	for node in body.get_children():
		body.remove_child(node)
		node.queue_free()
	controls.clear()
	log_view = null
	var heading := _row(body)
	var title := _label(("Game details" if detail and not selected.is_empty() else "Your library") if tab == 0 else ("Updates" if tab == 1 else "Settings"),38)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	heading.add_child(title)
	_button("Games" + ("  •" if tab == 0 else ""),func(): _switch_tab(0),heading,"games")
	_button("Updates" + ("  •" if tab == 1 else ""),func(): _switch_tab(1),heading,"updates")
	_button("Settings" + ("  •" if tab == 2 else ""),func(): _switch_tab(2),heading,"settings")
	if backend.busy: body.add_child(_label("Working…  Your changes are being read safely.",18,MINT))
	elif not error.is_empty():
		var alert := _card(body)
		_paragraph(alert,error,Color("ffbd9d"))
		_button("Try again",func(): _request(failed_command,failed_args),alert,"retry")
	elif not notice.is_empty(): body.add_child(_label(notice,18,MINT))
	if tab == 2: _settings_screen()
	elif tab == 1: _updates_screen()
	elif detail and not selected.is_empty(): _detail_screen()
	else: _games_screen()
	var count: int = status.get("pending",[]).size()
	pending_label.text = "%s pending change%s" % [count,"" if count == 1 else "s"] if count else "Everything is up to date  ·  No pending changes"
	apply_button.disabled = count == 0 or backend.busy
	if not apply_button.disabled: controls.append(apply_button)
	hints.keyboard = keyboard
	hints.detail = detail
	hints.library_preview = is_instance_valid(preview)
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
	var library := games if settings.get("show_games_without_recipe",true) else games.filter(func(game): return game.has_recipe)
	var visible := Nav.visible_games(library,filter)
	var split := _row(body)
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var scroll := DragScroll.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_stretch_ratio = 1.2
	scroll.follow_focus = true
	split.add_child(scroll)
	var list := VBoxContainer.new()
	list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(list)
	if visible.is_empty():
		_paragraph(list,"No games match this filter." if not games.is_empty() else "Your library is waiting. Install a Steam game, then refresh.")
		_button("Refresh library",_refresh,list,"refresh")
	for game in visible:
		var state := Text.fix(game)
		var launch: Variant = game.get("last_launch")
		var result := "Not launched" if launch == null else ("Last handoff OK" if launch.get("ok",false) else "Preparation failed")
		var summary: String = "Recipe " + str(game.get("recipe_version","")) + "   ·   " + state if game.has_recipe else state
		var text: String = game.name + "\n" + summary + "   ·   " + result
		var button := _button(text,_open_game.bind(game),list,game.appid)
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		button.add_theme_font_size_override("font_size",18)
		button.custom_minimum_size.y = 92
		button.focus_entered.connect(_focus_game.bind(game))
		button.mouse_entered.connect(_focus_game.bind(game))
	preview = _card(split)
	preview.get_parent().size_flags_horizontal = Control.SIZE_EXPAND_FILL
	# Clip/scroll long notes and mod lists inside the panel, keeping the footer fixed.
	preview_scroll = DragScroll.new()
	preview_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	preview.add_child(preview_scroll)
	var content := VBoxContainer.new()
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content.add_theme_constant_override("separation",8)
	preview_scroll.add_child(content)
	preview_art = _banner(content, 150)
	for spec in [["name",27,INK],["fix",18,MINT],["proton",18,INK],["mods",18,INK],["launch",16,MUTED],["note",18,MUTED]]:
		var label := _label("",spec[1],spec[2])
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		content.add_child(label)
		preview_fields[spec[0]] = label
	if games.is_empty():
		preview_fields.name.text = "Less setup. More play."
		preview_fields.note.text = "Install a Steam game, then refresh your library."
	elif visible.is_empty():
		preview_fields.name.text = "No matching games"
		preview_fields.note.text = "Try another library filter."
	else:
		var initial: Dictionary = visible[0]
		for game in visible:
			if game.appid == focused_game: initial = game
		_focus_game(initial)

func _banner(parent: Node, height: int) -> Control:
	var art := GameArt.new()
	art.cache = art_cache
	art.custom_minimum_size.y = height
	parent.add_child(art)
	return art

func _focus_game(game: Dictionary) -> void:
	focused_game = game.appid
	if not is_instance_valid(preview) or preview_id == game.appid: return
	preview_id = game.appid
	preview_scroll.scroll_vertical = 0
	preview_art.show_game(game)
	preview_fields.name.text = game.name
	preview_fields.fix.text = Text.fix(game)
	preview_fields.proton.text = Text.proton(game)
	preview_fields.mods.text = "\n".join(game.mods.map(func(mod): return Text.mod_label(mod))) if not game.mods.is_empty() else "No optional mods"
	preview_fields.launch.text = Text.launch(game)
	preview_fields.note.text = Text.note(game)
	_load_preview_note()

func _load_preview_note() -> void:
	if not is_instance_valid(preview) or preview_id.is_empty(): return
	for game in games:
		if game.appid != preview_id: continue
		if game.get("notes") is String: return
		preview_fields.note.text = preview_notes.get(preview_id,"Loading recipe notes…")
		if not preview_notes.has(preview_id) and not preview_backend.busy:
			preview_note_id = preview_id
			preview_backend.request("game",[preview_id])

func _preview_completed(_command: String, data: Dictionary) -> void:
	preview_notes[preview_note_id] = "Recipe notes unavailable." if data.has("error") else Text.note(data)
	if preview_notes.size() > 8: preview_notes.erase(preview_notes.keys()[0])
	_load_preview_note()

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
	var scroll := DragScroll.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	body.add_child(scroll)
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(column)
	var banner := _banner(column, 170)
	banner.show_game(selected)
	_paragraph(column,Text.fix(selected) + "  ·  " + Text.proton(selected),MINT)
	var mods := _card(column)
	mods.add_child(_label("Mods & recipe",23))
	if selected.mods.is_empty(): _paragraph(mods,"No optional mods in this recipe." if selected.has_recipe else "A recipe isn’t available yet. Steam will launch this game normally.")
	for mod in selected.mods:
		var mod_button := _button(Text.mod_label(mod),_toggle_mod.bind(mod),mods,"mod"+mod.id)
		mod_button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		mod_button.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		mod_button.add_theme_font_size_override("font_size",19)
	var notes := _card(column)
	notes.add_child(_label("Recipe notes",23))
	_paragraph(notes,str(selected.get("notes","Loading recipe notes…")))
	var logs := _card(column)
	logs.add_child(_label("Last launch log",23))
	log_view = DragScroll.new()
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
	var column := _scroll_column()
	_update_status(column)

func _scroll_column() -> VBoxContainer:
	var scroll := DragScroll.new()
	screen_scroll = scroll
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	body.add_child(scroll)
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(column)
	return column

func _settings_screen() -> void:
	var column := _scroll_column()
	var splash_card := _card(column)
	splash_card.add_child(_label("General",23))
	_paragraph(splash_card,"See your Proton build, fixes and mods before the game starts. A, B or touch skips it.")
	var splash_enabled: bool = settings.get("splash",true)
	_button("Launch splash: On" if splash_enabled else "Launch splash: Off",func(): _request("settings",["--splash","off" if splash_enabled else "on"]),splash_card,"splash_toggle",not settings.is_empty())
	var duration_row := _row(splash_card)
	var seconds: float = settings.get("splash_seconds",2.5)
	_button("−  0.5 s",func(): _request("settings",["--splash-seconds",str(seconds-0.5)]),duration_row,"splash_less",seconds > 1 and not settings.is_empty())
	duration_row.add_child(_label("%.1f seconds" % seconds,20,MINT))
	_button("+  0.5 s",func(): _request("settings",["--splash-seconds",str(seconds+0.5)]),duration_row,"splash_more",seconds < 6 and not settings.is_empty())
	var defaults := splash_card
	defaults.add_child(_label("Default for all games",23))
	var pending_actions: Array = status.get("pending",[]).map(func(p): return p.get("action",""))
	var is_default: bool = status.get("default_tool",false)
	if pending_actions.has("default_on"): is_default = true
	elif pending_actions.has("default_off"): is_default = false
	_paragraph(defaults,"Use DeckCompanion for Windows games without their own Steam compatibility setting; Apply restarts Steam.")
	_button("Stop using as default" if is_default else "Use DeckCompanion for all games",func(): _request("default",["--off" if is_default else "--on"]),defaults,"default")
	var library := _card(column)
	library.add_child(_label("Library",23))
	_setting_toggle(library,"show_games_without_recipe","Show games without a recipe","Keep every installed game visible, including games with no DeckCompanion recipe.")
	var updates := _card(column)
	updates.add_child(_label("Updates",23))
	_setting_toggle(updates,"check_updates_on_start","Check when DeckCompanion opens","Check the engine and recipe index in the background when the UI opens.")
	_button("Checking…" if update_backend.busy else "Check now",_check_updates,updates,"check",not update_backend.busy)
	var about := _card(column)
	about.add_child(_label("About",23))
	_paragraph(about,"Engine %s  ·  UI %s\nInstall path: %s" % [status.get("version","Not connected"),UI_VERSION,status.get("install_path","Not reported by engine")])
	_paragraph(about,"Close Steam, then run this command to uninstall DeckCompanion. Your data is preserved.")
	var path: String = str(status.get("install_path","~/.local/share/deckcompanion")).path_join("bin/deckcompanion")
	var command := _label("'%s' uninstall --json" % path.replace("'","'\\''"),17,MINT)
	command.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	about.add_child(command)
	_button("MIT license",_show_license.bind(false),about,"license")
	_button("Third-party notices",_show_license.bind(true),about,"notices")

func _setting_toggle(parent: Node, key: String, title: String, explanation: String) -> void:
	_paragraph(parent,explanation)
	var enabled: bool = settings.get(key,true)
	_button(title + (": On" if enabled else ": Off"),func(): _request("settings",["--set",key+"="+str(not enabled).to_lower()]),parent,key,not settings.is_empty())

func _check_updates() -> void:
	if not update_backend.busy: update_backend.request("update")

func _update_completed(_command: String, data: Dictionary) -> void:
	if data.has("error"): notice = "Update check: " + str(data.error.message)
	else:
		update = data
		notice = "Update check complete."
	if tab != 0: _render()

func _show_license(third_party: bool) -> void:
	for child in licenses.get_children():
		if child is ScrollContainer: licenses.remove_child(child); child.queue_free()
	var scroll := DragScroll.new()
	license_scroll = scroll
	scroll.custom_minimum_size = Vector2(700,420)
	licenses.add_child(scroll)
	var text := RichTextLabel.new()
	text.fit_content = true
	text.scroll_active = false
	text.selection_enabled = true
	text.custom_minimum_size.x = 680
	text.text = FileAccess.get_file_as_string("res://legal/notices.txt" if third_party else "res://legal/mit.txt")
	if third_party: text.text += "\n\nGodot Engine\n" + Engine.get_license_text() + "\n\n" + JSON.stringify(Engine.get_license_info(),"  ")
	scroll.add_child(text)
	licenses.title = "Third-party notices" if third_party else "MIT license"
	modal_focus = get_viewport().gui_get_focus_owner()
	licenses.popup_centered()
	licenses.get_ok_button().grab_focus()

func _update_status(column: Node) -> void:
	var engine := _card(column)
	engine.add_child(_label("Engine  /  " + str(status.get("version","Not connected")),26))
	var available: String = str(update.get("engine",{}).get("available","Not checked"))
	_paragraph(engine,"Available version: " + available + "   ·   Steam " + ("is running" if status.get("steam_running",false) else "is not running"))
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
	last_focus = ["games","updates","settings"][value]
	_render()

func _back() -> void:
	if backend.busy: return
	if detail:
		detail = false
		last_focus = selected.get("appid","")
	elif tab != 0: tab = 0
	_render()

func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed: keyboard = true
	elif event is InputEventJoypadButton or (event is InputEventJoypadMotion and absf(event.axis_value) > 0.3): keyboard = false
	if hints:
		hints.keyboard = keyboard
		hints.queue_redraw()

func _unhandled_input(event: InputEvent) -> void:
	if dialog.visible or licenses.visible or backend.busy: return
	if event.is_action_pressed("ui_cancel"): _back()
	elif event.is_action_pressed("dc_left") or event.is_action_pressed("dc_right"): _switch_tab(Nav.next_index(tab,-1 if event.is_action_pressed("dc_left") else 1,3))
	elif event.is_action_pressed("dc_x"):
		if detail: _toggle_game()
		elif tab == 0: _set_filter(Nav.next_index(filter,1,3))
	elif event.is_action_pressed("dc_y"): _confirm()
	else: return
	get_viewport().set_input_as_handled()

func _process(delta: float) -> void:
	var scroll: ScrollContainer = license_scroll if licenses.visible else (log_view if detail else (screen_scroll if tab != 0 else preview_scroll))
	if is_instance_valid(scroll) and not dialog.visible:
		var axis := 0.0
		for device in Input.get_connected_joypads():
			var value := Input.get_joy_axis(device,JOY_AXIS_RIGHT_Y)
			if absf(value) > absf(axis): axis = value
		if Input.is_physical_key_pressed(KEY_PAGEDOWN): axis = 1.0
		if Input.is_physical_key_pressed(KEY_PAGEUP): axis = -1.0
		if absf(axis) > 0.25: scroll.scroll_vertical += int(axis*420*delta)

func _capture_if_requested() -> void:
	var directory := ""
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--capture-dir="): directory = arg.trim_prefix("--capture-dir=")
	if directory.is_empty() or not backend.mock: return
	DirAccess.make_dir_recursive_absolute(directory)
	for screen in ["games","detail","updates","settings"]:
		if screen == "games":
			await get_tree().create_timer(0.6).timeout
			for control in controls:
				if control.get_meta("focus_id","") == games[0].appid: control.grab_focus()
		if screen == "detail": _open_game(games[0])
		if screen == "updates":
			_switch_tab(1)
			_request("update")
		if screen == "settings": _switch_tab(2)
		await get_tree().create_timer(1.0).timeout
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(directory.path_join(screen+".png"))
	get_tree().quit()
