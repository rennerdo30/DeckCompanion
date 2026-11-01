extends Control
const Backend = preload("res://scripts/backend.gd")
const Nav = preload("res://scripts/navigation.gd")
const Hints = preload("res://scripts/hints.gd")
const ArtCache = preload("res://scripts/art_cache.gd")
const GameArt = preload("res://scripts/game_art.gd")
const Text = preload("res://scripts/game_text.gd")
const DragScroll = preload("res://scripts/drag_scroll.gd")
const AddGame = preload("res://scripts/add_game.gd")
var add_flow: RefCounted
var remove_dialog: ConfirmationDialog
var restore_dialog: ConfirmationDialog
var proton_remove_dialog: ConfirmationDialog
var pending_proton_remove := ""
var available_data: Dictionary = {}
var decky_backend: Node
var decky_status: Dictionary = {}
var plugin_status: Dictionary = {}
var decky_handoff: Dictionary = {}
var decky_dialog: ConfirmationDialog
var launcher_backend: Node
var launchers_data: Array = []
var launcher_poll := 0.0
const UI_VERSION := "0.4.5"
# Design tokens (shared with the logo and Steam artwork palette).
const INK := Color("e7f1f4")
const MUTED := Color("8fa6b2")
const MINT := Color("b8f18b")
const CREAM := Color("e9d9a0")
const WARN := Color("ffbd9d")
const SURFACE := Color("101c25")
const SURFACE_2 := Color("16262f")
const SURFACE_3 := Color("1d3240")
const LINE := Color("24404c")
const FILTERS := ["All", "Has fix", "Enabled", "Non-Steam", "Attention"]
var sort_by_name := false
var status_chip: Button
var notice_row: HBoxContainer
var notice_label: Label
var tab_bar: HBoxContainer
var ui_config := ConfigFile.new()
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
var proton_backend: Node
var proton_data: Dictionary = {}
var proton_poll := 0.0
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
	decky_backend = Backend.new()
	add_child(decky_backend)
	decky_backend.completed.connect(_decky_completed)
	launcher_backend = Backend.new()
	add_child(launcher_backend)
	launcher_backend.completed.connect(_launchers_completed)
	proton_backend = Backend.new()
	add_child(proton_backend)
	proton_backend.completed.connect(_proton_completed)
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

func _style(bg: Color, border: Color = Color.TRANSPARENT, radius := 10, pad_x := 14, pad_y := 8) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = bg
	box.border_color = border
	box.set_border_width_all(2 if border.a else 0)
	box.set_corner_radius_all(radius)
	box.content_margin_left = pad_x
	box.content_margin_right = pad_x
	box.content_margin_top = pad_y
	box.content_margin_bottom = pad_y
	return box

func _make_theme() -> void:
	theme = Theme.new()
	theme.default_font_size = 19
	theme.set_color("font_color","Label",INK)
	for state in ["normal","hover","pressed","disabled"]:
		theme.set_stylebox(state,"Button",_style(SURFACE_3 if state != "hover" else Color("27434f")))
		theme.set_color("font_color" + ("" if state == "normal" else "_"+state),"Button",INK if state != "disabled" else Color("5d7380"))
	theme.set_stylebox("focus","Button",_style(Color(0.72,0.95,0.55,0.10),MINT))
	theme.set_stylebox("panel","AcceptDialog",_style(Color("122530"),Color("4f6f6a"),14,20,16))
	theme.set_constant("separation","VBoxContainer",10)
	theme.set_constant("separation","HBoxContainer",10)
	theme.set_color("font_color","CheckButton",INK)

func _label(text: String, size_px := 19, color := INK) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size",size_px)
	label.add_theme_color_override("font_color",color)
	return label

func _button(text: String, action: Callable, parent: Node, id: String, enabled := true) -> Button:
	var button := Button.new()
	button.text = text
	button.custom_minimum_size.y = 40
	button.disabled = not enabled or backend.busy
	button.set_meta("focus_id",id)
	button.pressed.connect(action)
	button.focus_entered.connect(func(): last_focus = id)
	parent.add_child(button)
	if not button.disabled: controls.append(button)
	return button

## Compact pill used for tabs, filters and the status chip.
func _chip(text: String, action: Callable, parent: Node, id: String, active := false, accent := MINT, enabled := true) -> Button:
	var button := _button(text,action,parent,id,enabled)
	button.custom_minimum_size.y = 40
	button.add_theme_font_size_override("font_size",17)
	var fill := Color(accent.r,accent.g,accent.b,0.16) if active else Color(0,0,0,0)
	for state in ["normal","pressed","disabled"]:
		button.add_theme_stylebox_override(state,_style(fill,Color(accent.r,accent.g,accent.b,0.55) if active else LINE,17,14,4))
	button.add_theme_stylebox_override("hover",_style(Color(1,1,1,0.06),LINE,17,14,4))
	button.add_theme_stylebox_override("focus",_style(Color(0.72,0.95,0.55,0.10),MINT,17,14,4))
	button.add_theme_color_override("font_color",accent if active else INK)
	return button

## Small read-only pill (status badges).
func _badge(parent: Node, text: String, color := MINT) -> Label:
	var label := _label(text,15,color)
	var box := _style(Color(color.r,color.g,color.b,0.14),Color(0,0,0,0),10,9,2)
	label.add_theme_stylebox_override("normal",box)
	parent.add_child(label)
	return label

func _row(parent: Node) -> HBoxContainer:
	var row := HBoxContainer.new()
	parent.add_child(row)
	return row

func _card(parent: Node, title := "") -> VBoxContainer:
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel",_style(SURFACE,Color(0,0,0,0),14,16,12))
	parent.add_child(panel)
	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation",12)
	panel.add_child(column)
	if not title.is_empty(): column.add_child(_label(title.to_upper(),15,MINT))
	return column

func _paragraph(parent: Node, text: String, color := MUTED, size_px := 17) -> Label:
	var label := _label(text,size_px,color)
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	parent.add_child(label)
	return label

static func _ago(unix: Variant) -> String:
	if unix == null or int(unix) <= 0: return "Never played"
	var minutes := int((Time.get_unix_time_from_system() - float(unix)) / 60.0)
	if minutes < 2: return "Played just now"
	if minutes < 60: return "Played %d min ago" % minutes
	if minutes < 48 * 60: return "Played %d h ago" % (minutes / 60)
	return "Played %d days ago" % (minutes / 1440)

func _build_shell() -> void:
	if ui_config.load("user://ui.cfg") == OK:
		filter = clampi(int(ui_config.get_value("library","filter",0)),0,FILTERS.size()-1)
		sort_by_name = bool(ui_config.get_value("library","sort_by_name",false))
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left","right"]: margin.add_theme_constant_override("margin_"+side,20)
	margin.add_theme_constant_override("margin_top",12)
	margin.add_theme_constant_override("margin_bottom",6)
	add_child(margin)
	root = VBoxContainer.new()
	root.add_theme_constant_override("separation",8)
	margin.add_child(root)
	# One slim bar: brand, tabs, status and the way back to Steam (DC-31, DC-25).
	var bar := _row(root)
	bar.custom_minimum_size.y = 48
	bar.add_theme_constant_override("separation",8)
	var logo := TextureRect.new()
	logo.texture = load("res://icon.svg")
	logo.custom_minimum_size = Vector2(34,34)
	logo.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	logo.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	logo.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	bar.add_child(logo)
	var brand := _label("DeckCompanion",20,INK)
	brand.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	bar.add_child(brand)
	var gap := Control.new()
	gap.custom_minimum_size.x = 14
	bar.add_child(gap)
	tab_bar = _row(bar)
	tab_bar.add_theme_constant_override("separation",6)
	tab_bar.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bar.add_child(spacer)
	if backend.mock: _badge(bar,"MOCK DATA",CREAM).size_flags_vertical = Control.SIZE_SHRINK_CENTER
	status_chip = Button.new()
	status_chip.set_meta("focus_id","status")
	status_chip.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	status_chip.pressed.connect(_confirm)
	status_chip.focus_entered.connect(func(): last_focus = "status")
	bar.add_child(status_chip)
	var steam := Button.new()
	steam.text = "Back to Steam"
	steam.set_meta("focus_id","steam")
	steam.custom_minimum_size.y = 40
	steam.add_theme_font_size_override("font_size",17)
	steam.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	steam.pressed.connect(_back_to_steam)
	steam.focus_entered.connect(func(): last_focus = "steam")
	bar.add_child(steam)
	apply_button = steam  # Kept for tests that look up a shell button; Apply now lives in the status chip.
	var rule := ColorRect.new()
	rule.color = LINE
	rule.custom_minimum_size.y = 1
	root.add_child(rule)
	notice_row = _row(root)
	notice_label = _label("",17,MINT)
	notice_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	notice_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	notice_row.add_child(notice_label)
	body = VBoxContainer.new()
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_theme_constant_override("separation",10)
	root.add_child(body)
	pending_label = notice_label
	hints = Hints.new()
	hints.custom_minimum_size.y = 36
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
	restore_dialog = ConfirmationDialog.new()
	restore_dialog.title = "Restore previous Steam settings?"
	restore_dialog.dialog_text = "Every game goes back to the compatibility tool it had before DeckCompanion took over.
\"Use DeckCompanion for all games\" turns off. Apply restarts Steam once."
	restore_dialog.ok_button_text = "Restore all"
	restore_dialog.cancel_button_text = "Keep DeckCompanion"
	restore_dialog.min_size = Vector2i(720,260)
	restore_dialog.confirmed.connect(func(): _request("restore-all"))
	restore_dialog.canceled.connect(_restore_modal_focus)
	add_child(restore_dialog)
	decky_dialog = ConfirmationDialog.new()
	decky_dialog.title = "Install Decky Loader?"
	decky_dialog.dialog_text = "Decky needs your password once.\nDeckCompanion switches to Desktop Mode and opens the official installer.\nWhen it is done, tap \"Return to Gaming Mode\" on the desktop."
	decky_dialog.ok_button_text = "Switch and install"
	decky_dialog.cancel_button_text = "Not now"
	decky_dialog.min_size = Vector2i(720,260)
	decky_dialog.confirmed.connect(_start_decky_install)
	decky_dialog.canceled.connect(_restore_modal_focus)
	add_child(decky_dialog)
	proton_remove_dialog = ConfirmationDialog.new()
	proton_remove_dialog.title = "Remove Proton build?"
	proton_remove_dialog.ok_button_text = "Remove"
	proton_remove_dialog.cancel_button_text = "Keep it"
	proton_remove_dialog.min_size = Vector2i(640,220)
	proton_remove_dialog.confirmed.connect(func(): proton_backend.request("proton",["remove",pending_proton_remove,"--force"]))
	proton_remove_dialog.canceled.connect(_restore_modal_focus)
	add_child(proton_remove_dialog)
	remove_dialog = ConfirmationDialog.new()
	remove_dialog.title = "Remove this game?"
	remove_dialog.dialog_text = "Removes only the Steam shortcut and library entry after Apply.\nYour game files and save prefix are kept."
	remove_dialog.ok_button_text = "Remove game"
	remove_dialog.confirmed.connect(func(): _request("remove-game",[selected.slug]))
	remove_dialog.canceled.connect(_restore_modal_focus)
	add_child(remove_dialog)

func _back_to_steam() -> void:
	# Steam brings its library back as soon as this non-Steam app exits.
	get_tree().quit()

func _save_ui_config() -> void:
	ui_config.set_value("library","filter",filter)
	ui_config.set_value("library","sort_by_name",sort_by_name)
	ui_config.save("user://ui.cfg")

func _launchers_completed(command: String, data: Dictionary) -> void:
	if data.has("error"): notice = "Launchers: " + str(data.error.message)
	elif command == "launchers": launchers_data = data.launchers
	else:
		if data.get("scheduled",false): notice = "Installing %s in the background. You can keep using DeckCompanion." % data.launcher.name
		elif data.get("removed",false): notice = "%s removed. Its prefix and saves are kept." % data.launcher.name
		launcher_backend.request.call_deferred("launchers")
	if add_flow != null: _render()

func _decky_completed(_command: String, data: Dictionary) -> void:
	var action: String = str(decky_backend.get_meta("action",""))
	if data.has("error"): notice = "Decky: " + str(data.error.message)
	elif action == "status": decky_status = data
	elif action == "plugin-status": plugin_status = data
	elif action in ["install", "install-desktop"]:
		decky_handoff = data
		if data.get("switching",false): notice = "Switching to Desktop Mode. The Decky installer opens there."
		elif data.get("launched",false): notice = "The Decky installer opened in Konsole. Enter your password there."
		else: notice = "Open Desktop Mode to install Decky; the installer needs your password."
	elif action == "plugin-install":
		notice = "DeckCompanion plugin %s. Reload plugins in Decky." % ("installed" if data.get("changed",false) else "is already up to date")
		_request_decky("plugin-status")
		return
	if action == "status": _request_decky.call_deferred("plugin-status")
	if tab == 2: _render()

func _request_decky(action: String) -> void:
	if decky_backend.busy: return
	decky_backend.set_meta("action",action)
	decky_backend.request("decky",[action])

func _decky_card(column: Node) -> void:
	var card := _card(column,"Decky")
	var installed: bool = decky_status.get("installed",false)
	var current := str(decky_status.get("version","")).trim_prefix("v")
	var latest_value: Variant = decky_status.get("latest",{}).get("version")
	var latest := str(latest_value).trim_prefix("v") if latest_value != null else ""
	var newer := installed and latest != "" and current != "" and _version_less(current,latest)
	var line := "Checking…"
	if not decky_status.is_empty():
		if not installed: line = "Not installed  ·  adds plugins to the Quick Access Menu"
		elif newer: line = "Version %s installed  ·  %s available" % [current,latest]
		else: line = "Version %s  ·  up to date" % current if latest != "" else "Version %s installed" % current
	var loader_row := _setting_row(card,"Decky Loader",line)
	if not decky_status.is_empty() and (not installed or newer):
		_chip("Update" if installed else "Install",_confirm_decky,loader_row,"decky_install",false,MINT,not decky_backend.busy).size_flags_vertical = Control.SIZE_SHRINK_CENTER
	# The QAM plugin row appears once a plugin release exists (or it is installed).
	var plugin_latest: Variant = plugin_status.get("latest",{}).get("version")
	if plugin_latest != null or plugin_status.get("installed",false):
		var plugin_line := ("Version %s installed" % str(plugin_status.get("version",""))) if plugin_status.get("installed",false) else "Not installed"
		if plugin_latest != null and plugin_status.get("version") != plugin_latest: plugin_line += "  ·  %s available" % str(plugin_latest)
		var plugin_row := _setting_row(card,"DeckCompanion in the Quick Access Menu",plugin_line)
		var plugin_newer: bool = plugin_latest != null and plugin_status.get("version") != plugin_latest
		if plugin_status.get("can_install",false) and plugin_newer:
			_chip("Update" if plugin_status.get("installed",false) else "Install",func(): _request_decky("plugin-install"),plugin_row,"plugin_install",false,MINT,not decky_backend.busy).size_flags_vertical = Control.SIZE_SHRINK_CENTER
		elif plugin_newer and not plugin_status.get("can_install",false):
			_paragraph(card,"Install it from Decky's settings: Developer › Install Plugin from ZIP.",MUTED,15)

static func _version_less(a: String, b: String) -> bool:
	var pa := a.split(".")
	var pb := b.split(".")
	for i in maxi(pa.size(),pb.size()):
		var x := int(pa[i]) if i < pa.size() else 0
		var y := int(pb[i]) if i < pb.size() else 0
		if x != y: return x < y
	return false

func _confirm_decky() -> void:
	modal_focus = get_viewport().gui_get_focus_owner()
	decky_dialog.popup_centered()
	decky_dialog.get_cancel_button().grab_focus()

func _start_decky_install() -> void:
	# In Game Mode the installer must run on the desktop (it asks for your password there).
	var game_mode: bool = OS.get_environment("XDG_CURRENT_DESKTOP").to_lower().contains("gamescope") or backend.mock
	_request_decky("install-desktop" if game_mode else "install")

## Engine instructions may be a list or one string; never iterate a string per character.
static func _lines(value: Variant) -> Array:
	if value is String: return [value] if not value.strip_edges().is_empty() else []
	return value if value is Array else []

func launchers_busy() -> bool:
	return launchers_data.any(func(l): return l.get("state") == "installing")

func _confirm_restore() -> void:
	modal_focus = get_viewport().gui_get_focus_owner()
	restore_dialog.popup_centered()
	restore_dialog.get_cancel_button().grab_focus()

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
			"add-game", "remove-game":
				add_flow = null
				detail = false
				filter = 0
				last_focus = data.get("appid","add-game")
				notice = "Game added. Apply to add its Steam shortcut." if command == "add-game" else "Game removed. Apply to remove its Steam shortcut. Saves kept."
				_refresh()
				return
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
			"restore-all":
				notice = "Restoring %d game%s. Apply to restart Steam once." % [int(data.get("restoring",0)),"" if int(data.get("restoring",0)) == 1 else "s"]
				settings.clear()
				_request("status")
				return
			"game": selected = data
			"enable", "disable", "mod", "game-option":
				selected.merge(data,true)
				for i in games.size():
					if games[i].appid == data.appid: games[i] = data
				notice = Text.mod_message(data) if command == "mod" else ("Saved. Used from the next launch." if command == "game-option" and "--proton" in failed_args else "Change saved. Apply when you’re ready.")
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
	for node in tab_bar.get_children():
		tab_bar.remove_child(node)
		node.queue_free()
	for i in 3:
		_chip(["Library","Updates","Settings"][i],_switch_tab.bind(i),tab_bar,["games","updates","settings"][i],tab == i)
	for node in notice_row.get_children():
		if node != notice_label:
			notice_row.remove_child(node)
			node.queue_free()
	notice_label.add_theme_color_override("font_color",WARN if not error.is_empty() else MINT)
	notice_label.text = "Working…" if backend.busy else (error if not error.is_empty() else notice)
	if not error.is_empty() and not backend.busy:
		_chip("Try again",func(): _request(failed_command,failed_args),notice_row,"retry",false,WARN)
	notice_row.visible = not notice_label.text.is_empty()
	if tab == 2: _settings_screen()
	elif tab == 1: _updates_screen()
	elif add_flow != null: add_flow.render()
	elif detail and not selected.is_empty(): _detail_screen()
	else: _games_screen()
	_update_status_chip()
	hints.keyboard = keyboard
	hints.detail = detail
	hints.library_preview = is_instance_valid(preview)
	hints.queue_redraw()
	Nav.wire(controls)
	_restore_focus.call_deferred()
	_wire_top_row.call_deferred()

## Corner status: a quiet checkmark when nothing waits, an Apply call to action otherwise.
func _update_status_chip() -> void:
	var count: int = status.get("pending",[]).size()
	var waiting: bool = count > 0 and not backend.busy
	status_chip.text = ("●  %d change%s · Apply" % [count,"" if count == 1 else "s"]) if count > 0 else "✓  Up to date"
	status_chip.disabled = not waiting
	status_chip.focus_mode = Control.FOCUS_ALL if waiting else Control.FOCUS_NONE
	status_chip.custom_minimum_size.y = 40
	status_chip.add_theme_font_size_override("font_size",17)
	var accent := MINT if waiting else MUTED
	for state in ["normal","pressed","disabled","hover"]:
		status_chip.add_theme_stylebox_override(state,_style(Color(accent.r,accent.g,accent.b,0.16 if waiting else 0.0),Color(accent.r,accent.g,accent.b,0.6) if waiting else Color(0,0,0,0),17,14,4))
	status_chip.add_theme_stylebox_override("focus",_style(Color(0.72,0.95,0.55,0.10),MINT,17,14,4))
	for key in ["font_color","font_disabled_color","font_hover_color","font_pressed_color"]:
		status_chip.add_theme_color_override(key,accent)
	if waiting: controls.append(status_chip)

const SHELL_IDS := ["games","updates","settings","status","steam","retry"]

## Up from the first content row goes to the active tab, not to whatever top-bar
## button happens to be geometrically closest.
func _wire_top_row() -> void:
	await get_tree().process_frame
	var tab_chip: Control = null
	for chip in tab_bar.get_children():
		if chip is Control and chip.get_meta("focus_id","") == ["games","updates","settings"][tab]: tab_chip = chip
	if tab_chip == null: return
	var content: Array = controls.filter(func(c): return is_instance_valid(c) and c.is_visible_in_tree() and not SHELL_IDS.has(c.get_meta("focus_id","")))
	if content.is_empty(): return
	var top_y: float = content.map(func(c): return c.get_global_rect().position.y).min()
	for control in content:
		if control.get_global_rect().position.y <= top_y + 12: control.focus_neighbor_top = control.get_path_to(tab_chip)
	tab_chip.focus_neighbor_bottom = tab_chip.get_path_to(content.filter(func(c): return c.get_global_rect().position.y <= top_y + 12)[0])

func _restore_focus() -> void:
	if not controls.is_empty():
		# Start on the content, not on the tab bar.
		var target: Control = controls[0]
		for control in controls:
			if not SHELL_IDS.has(control.get_meta("focus_id","")):
				target = control
				break
		for control in controls:
			if control.get_meta("focus_id","") == last_focus: target = control
		if is_instance_valid(target) and target.is_inside_tree(): target.grab_focus()

func _games_screen() -> void:
	# Toolbar: filters, sort and Add game in one compact row (DC-24).
	var toolbar := _row(body)
	toolbar.add_theme_constant_override("separation",6)
	for i in FILTERS.size():
		_chip(FILTERS[i],_set_filter.bind(i),toolbar,"filter%s" % i,filter == i)
	var push := Control.new()
	push.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toolbar.add_child(push)
	_chip("Sort: " + ("A–Z" if sort_by_name else "Recent"),_toggle_sort,toolbar,"sort")
	_chip("＋  Add game",func(): notice = ""; error = ""; add_flow = AddGame.new(self); launcher_backend.request("launchers"); _render(),toolbar,"add-game",true,CREAM)
	var library := games if settings.get("show_games_without_recipe",true) else games.filter(func(game): return game.has_recipe or game.get("kind") == "non_steam")
	var visible := Nav.sort_games(Nav.visible_games(library,filter),sort_by_name)
	# Land on the most relevant game, not on the filter row.
	if (last_focus.is_empty() or SHELL_IDS.has(last_focus)) and not visible.is_empty(): last_focus = visible[0].appid
	var split := _row(body)
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	split.add_theme_constant_override("separation",14)
	var scroll := DragScroll.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_stretch_ratio = 1.15
	scroll.follow_focus = true
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	split.add_child(scroll)
	var list := VBoxContainer.new()
	list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	list.add_theme_constant_override("separation",6)
	scroll.add_child(list)
	if visible.is_empty():
		_paragraph(list,"No games match this filter." if not games.is_empty() else "Your library is waiting. Install a Steam game, then refresh.",MUTED,16)
		_chip("Refresh library",_refresh,list,"refresh")
	var active_filter: Control = toolbar.get_child(filter)
	for game in visible:
		var row := _game_row(list,game)
		if game == visible[0]: row.focus_neighbor_top = row.get_path_to(active_filter)
	_preview_panel(split,visible)

## One dense library row: thumbnail, name, one status line and a badge.
func _game_row(list: Node, game: Dictionary) -> Button:
	var button := _button("",_open_game.bind(game),list,game.appid)
	button.custom_minimum_size.y = 74
	button.add_theme_stylebox_override("normal",_style(SURFACE,Color(0,0,0,0),12,8,6))
	button.add_theme_stylebox_override("hover",_style(SURFACE_2,Color(0,0,0,0),12,8,6))
	button.add_theme_stylebox_override("focus",_style(Color(0.72,0.95,0.55,0.10),MINT,12,8,6))
	button.focus_entered.connect(_focus_game.bind(game))
	button.mouse_entered.connect(_focus_game.bind(game))
	var inner := HBoxContainer.new()
	inner.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	inner.offset_left = 8
	inner.offset_right = -12
	inner.offset_top = 6
	inner.offset_bottom = -6
	inner.add_theme_constant_override("separation",12)
	inner.mouse_filter = Control.MOUSE_FILTER_IGNORE
	button.add_child(inner)
	var thumb := GameArt.new()
	thumb.cache = art_cache
	thumb.custom_minimum_size = Vector2(128,60)
	inner.add_child(thumb)
	thumb.show_game(game)
	var text := VBoxContainer.new()
	text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	text.alignment = BoxContainer.ALIGNMENT_CENTER
	text.add_theme_constant_override("separation",2)
	text.mouse_filter = Control.MOUSE_FILTER_IGNORE
	inner.add_child(text)
	var name_label := _label(game.name,20,INK)
	name_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	name_label.clip_text = true
	text.add_child(name_label)
	var line := _label(_row_line(game),16,MUTED)
	line.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	line.clip_text = true
	text.add_child(line)
	var badge := _row_badge(game)
	if not badge.is_empty():
		var label := _badge(inner,badge[0],badge[1])
		label.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	for node in inner.find_children("*","Control",true,false): node.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return button

func _row_line(game: Dictionary) -> String:
	var parts: Array = []
	if game.get("kind") == "non_steam": parts.append("Non-Steam · umu")
	elif game.get("has_recipe",false): parts.append(Text.fix(game))
	else: parts.append("Steam default")
	var build: Variant = game.get("proton")
	if build is Dictionary: parts.append(str(build.get("name","")))
	parts.append(_ago(game.get("last_played")))
	return "  ·  ".join(parts)

func _row_badge(game: Dictionary) -> Array:
	if Nav.needs_attention(game): return ["CHECK", WARN]
	if game.get("pending") != null: return ["PENDING", CREAM]
	if game.get("kind") == "non_steam": return ["NON-STEAM", CREAM]
	if game.get("has_recipe",false): return ["FIX ON" if game.get("enabled",false) else "FIX", MINT]
	return []

## Right-hand preview of the focused game. Nodes persist while focus moves (no flicker).
func _preview_panel(split: Node, visible: Array) -> void:
	var panel := PanelContainer.new()
	panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	panel.add_theme_stylebox_override("panel",_style(SURFACE,Color(0,0,0,0),14,0,0))
	split.add_child(panel)
	preview = VBoxContainer.new()
	preview.add_theme_constant_override("separation",0)
	panel.add_child(preview)
	preview_art = _banner(preview, 190)
	preview_scroll = DragScroll.new()
	preview_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	preview_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	preview.add_child(preview_scroll)
	var pad := MarginContainer.new()
	pad.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	for side in ["left","right"]: pad.add_theme_constant_override("margin_"+side,16)
	pad.add_theme_constant_override("margin_top",12)
	pad.add_theme_constant_override("margin_bottom",12)
	preview_scroll.add_child(pad)
	var content := VBoxContainer.new()
	content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	content.add_theme_constant_override("separation",6)
	pad.add_child(content)
	for spec in [["name",26,INK],["fix",17,MINT],["proton",17,INK],["mods",17,INK],["launch",16,MUTED],["note",17,MUTED]]:
		var label := _label("",spec[1],spec[2])
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		content.add_child(label)
		preview_fields[spec[0]] = label
	if games.is_empty():
		preview_fields.name.text = "Less setup. More play."
		preview_fields.note.text = "Install a Steam game, then refresh your library."
	elif visible.is_empty():
		preview_fields.name.text = "No matching games"
		preview_fields.note.text = "Try another filter."
	else:
		var initial: Dictionary = visible[0]
		for game in visible:
			if game.appid == focused_game: initial = game
		_focus_game(initial)

func _toggle_sort() -> void:
	sort_by_name = not sort_by_name
	last_focus = "sort"
	_save_ui_config()
	_render()

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
	# Hero: artwork with the name and state on top, back action in the corner.
	var hero := Control.new()
	hero.custom_minimum_size.y = 176
	hero.clip_contents = true
	body.add_child(hero)
	var art := GameArt.new()
	art.cache = art_cache
	art.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	hero.add_child(art)
	art.show_game(selected)
	var shade := TextureRect.new()
	var gradient := Gradient.new()
	gradient.set_color(0,Color(0.04,0.07,0.09,0.0))
	gradient.set_color(1,Color(0.04,0.07,0.09,0.92))
	var fill := GradientTexture2D.new()
	fill.gradient = gradient
	fill.fill_from = Vector2(0,0)
	fill.fill_to = Vector2(0,1)
	shade.texture = fill
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	shade.stretch_mode = TextureRect.STRETCH_SCALE
	shade.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hero.add_child(shade)
	var overlay := MarginContainer.new()
	overlay.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left","right"]: overlay.add_theme_constant_override("margin_"+side,16)
	overlay.add_theme_constant_override("margin_top",10)
	overlay.add_theme_constant_override("margin_bottom",12)
	hero.add_child(overlay)
	var stack := VBoxContainer.new()
	overlay.add_child(stack)
	var top := _row(stack)
	_chip("‹  Library",_back,top,"back")
	var grow := Control.new()
	grow.size_flags_vertical = Control.SIZE_EXPAND_FILL
	stack.add_child(grow)
	var name_label := _label(selected.name,32,INK)
	name_label.add_theme_color_override("font_shadow_color",Color(0,0,0,0.6))
	name_label.add_theme_constant_override("shadow_offset_y",2)
	stack.add_child(name_label)
	var chips := _row(stack)
	chips.add_theme_constant_override("separation",6)
	_badge(chips,Text.fix(selected),MINT if selected.get("has_recipe",false) else MUTED)
	_badge(chips,Text.proton(selected),INK)
	_badge(chips,_ago(selected.get("last_played")),MUTED)
	# Two columns use the full height: setup on the left, notes and log on the right.
	var columns := _row(body)
	columns.size_flags_vertical = Control.SIZE_EXPAND_FILL
	columns.add_theme_constant_override("separation",14)
	var left_scroll := DragScroll.new()
	left_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	left_scroll.follow_focus = true
	columns.add_child(left_scroll)
	screen_scroll = left_scroll
	var left := VBoxContainer.new()
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left_scroll.add_child(left)
	var setup := _card(left,"Setup")
	var enabled: bool = selected.get("pending") == "enable" or (selected.get("pending") == null and selected.enabled)
	if selected.get("kind") == "non_steam":
		var remove_row := _setting_row(setup,"Non-Steam game","Runs through umu-launcher with its own save prefix.")
		_chip("Remove game",func(): modal_focus = get_viewport().gui_get_focus_owner(); remove_dialog.popup_centered(); remove_dialog.get_cancel_button().grab_focus(),remove_row,"remove-game",false,WARN).size_flags_vertical = Control.SIZE_SHRINK_CENTER
	else:
		var takeover: String = str(selected.get("takeover","n/a"))
		if takeover != "n/a":
			var before: Variant = selected.get("previous_mapping")
			var hint := "Steam's own choice before: " + str(before) if before != null else "DeckCompanion picks Proton, fixes and mods."
			var takeover_row := _setting_row(setup,"Run through DeckCompanion",hint)
			_state_switch(takeover_row,"takeover",takeover == "on",func(): _request("game-option",[selected.appid,"--takeover","off" if takeover == "on" else "on"]))
		if selected.has_recipe:
			var toggle_row := _setting_row(setup,"Use DeckCompanion fixes","Applies this game's recipe and mods at launch.")
			_state_switch(toggle_row,"toggle",enabled,_toggle_game,true)
	_proton_picker(setup)
	var mods := _card(left,"Mods")
	if selected.mods.is_empty(): _paragraph(mods,"No optional mods in this recipe." if selected.has_recipe else "No mods for this game yet.")
	for mod in selected.mods:
		var mod_row := _setting_row(mods,"%s  %s" % [mod.get("name","Mod"),Text.version(mod.get("version"))],Text.mod_label(mod).get_slice(" — ",1))
		_state_switch(mod_row,"mod"+mod.id,mod.get("enabled",false),_toggle_mod.bind(mod)).tooltip_text = Text.mod_label(mod)
	var right := VBoxContainer.new()
	right.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	columns.add_child(right)
	var notes := _card(right,"Recipe notes")
	_paragraph(notes,str(selected.get("notes","Loading recipe notes…")) if str(selected.get("notes","")) != "" else "No notes.",INK)
	var logs := _card(right,"Last launch")
	logs.get_parent().size_flags_vertical = Control.SIZE_EXPAND_FILL
	logs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_paragraph(logs,Text.launch(selected),MUTED,16)
	log_view = DragScroll.new()
	log_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	log_view.custom_minimum_size.y = 90
	log_view.focus_mode = Control.FOCUS_ALL
	log_view.add_theme_stylebox_override("focus",_style(Color.TRANSPARENT,MINT))
	logs.add_child(log_view)
	controls.append(log_view)
	var log_text := _label("\n".join(selected.get("log_tail",[])) if not selected.get("log_tail",[]).is_empty() else "No launch log yet.",15,MUTED)
	log_text.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	log_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	log_view.add_child(log_text)

## Per-game Proton choice (DC-27): Automatic or any installed build.
func _proton_picker(parent: Node) -> void:
	var build: Variant = selected.get("proton")
	var reason: String = str(build.get("reason","")) if build is Dictionary else ""
	var valve: Variant = selected.get("valve_recommendation")
	var hint := Text.proton(selected) + ("  ·  Valve suggests " + str(valve) if valve != null and reason != "valve_recommended" else "")
	var row := _setting_row(parent,"Proton",hint)
	var picker := OptionButton.new()
	picker.custom_minimum_size = Vector2(250,42)
	picker.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	picker.add_theme_font_size_override("font_size",17)
	picker.get_popup().add_theme_font_size_override("font_size",19)
	picker.set_meta("focus_id","proton_pick")
	picker.add_item("Automatic")
	var names: Array = status.get("proton",[]).map(func(p): return str(p.get("name","")))
	for name in names: picker.add_item(name)
	picker.selected = names.find(str(build.get("name",""))) + 1 if reason == "user_override" and build is Dictionary else 0
	picker.item_selected.connect(func(index): _request("game-option",[selected.appid,"--proton","auto" if index == 0 else names[index-1]]))
	picker.focus_entered.connect(func(): last_focus = "proton_pick")
	picker.disabled = backend.busy or selected.get("kind") == "non_steam" and names.is_empty()
	row.add_child(picker)
	if not picker.disabled: controls.append(picker)

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
	var columns := _two_columns()
	var general := _card(columns[0],"General")
	var counts: Dictionary = status.get("takeover_counts",{})
	var takeover_on: bool = settings.get("takeover_all",true)
	var takeover_row := _setting_row(general,"Use DeckCompanion for all games","Every installed Windows game, including ones with their own Steam setting. Turning it off restores each game's previous tool." + (" (%d of %d games)" % [int(counts.get("on",0)),int(counts.get("eligible",0))] if counts.has("eligible") else ""))
	_state_switch(takeover_row,"takeover_all",takeover_on,func(): _request("settings",["--set","takeover_all="+("off" if takeover_on else "on")]),not settings.is_empty())
	var snapshot: Variant = status.get("takeover_snapshot")
	if snapshot is Dictionary:
		var when := str(snapshot.get("taken_at","")).get_slice("T",0)
		var restore_row := _setting_row(general,"Restore previous Steam settings","Saved %s for %d game%s. Turns off \"all games\" and puts every game back on its old tool." % [when,int(snapshot.get("games",0)),"" if int(snapshot.get("games",0)) == 1 else "s"])
		_chip("Restore all",_confirm_restore,restore_row,"restore_all",false,WARN).size_flags_vertical = Control.SIZE_SHRINK_CENTER
	var splash_enabled: bool = settings.get("splash",true)
	var splash_row := _setting_row(general,"Launch splash","Shows Proton, fixes and mods before a game starts. A skips, Y holds.")
	_state_switch(splash_row,"splash_toggle",splash_enabled,func(): _request("settings",["--splash","off" if splash_enabled else "on"]),not settings.is_empty())
	var duration_row := _setting_row(general,"Splash duration","The game starts automatically after this time.")
	var stepper := _row(duration_row)
	stepper.add_theme_constant_override("separation",4)
	stepper.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	var seconds: float = settings.get("splash_seconds",4.0)
	_chip("−",func(): _request("settings",["--splash-seconds",str(seconds-0.5)]),stepper,"splash_less",false,MINT,seconds > 1 and not settings.is_empty())
	var duration_value := _label("%.1f s" % seconds,19,MINT)
	duration_value.custom_minimum_size.x = 56
	duration_value.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	duration_value.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	stepper.add_child(duration_value)
	_chip("+",func(): _request("settings",["--splash-seconds",str(seconds+0.5)]),stepper,"splash_more",false,MINT,seconds < 10 and not settings.is_empty())
	var pending_actions: Array = status.get("pending",[]).map(func(p): return p.get("action",""))
	var is_default: bool = status.get("default_tool",false)
	if pending_actions.has("default_on"): is_default = true
	elif pending_actions.has("default_off"): is_default = false
	var defaults := _setting_row(general,"Default for new games","Steam's default tool for games installed later. Apply restarts Steam.")
	_state_switch(defaults,"default",is_default,func(): _request("default",["--off" if is_default else "--on"]))
	var library := _card(columns[0],"Library")
	_setting_toggle(library,"show_games_without_recipe","Show games without a recipe","Keep every installed game in the library.")
	var updates := _card(columns[1],"Updates")
	_setting_toggle(updates,"check_updates_on_start","Check when DeckCompanion opens","Looks for a newer engine and recipes in the background.")
	_setting_toggle(updates,"auto_install_ge","Install newest GE-Proton","Recipe pins are always installed; this also keeps the newest GE.")
	_setting_toggle(updates,"cleanup_old_ge","Clean up old GE-Proton","Removes only builds DeckCompanion installed and no longer needs.")
	var check_row := _setting_row(updates,"Check for updates","Engine, recipes and Proton builds.")
	_chip("Checking…" if update_backend.busy else "Check now",_check_updates,check_row,"check").size_flags_vertical = Control.SIZE_SHRINK_CENTER
	_decky_card(columns[1])
	var about := _card(columns[1],"About")
	_paragraph(about,"Engine %s  ·  UI %s\n%s" % [status.get("version","not connected"),UI_VERSION,status.get("install_path","Install path not reported")])
	_paragraph(about,"Uninstall (close Steam first, your data is kept):",MUTED,16)
	var path: String = str(status.get("install_path","~/.local/share/deckcompanion")).path_join("bin/deckcompanion")
	var command := _label("'%s' uninstall --json" % path.replace("'","'\\''"),16,MINT)
	command.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	about.add_child(command)
	var legal := _row(about)
	legal.add_theme_constant_override("separation",6)
	_chip("MIT license",_show_license.bind(false),legal,"license")
	_chip("Third-party notices",_show_license.bind(true),legal,"notices")

## A scrolling two-column page; returns [left, right] columns.
func _two_columns() -> Array:
	var scroll := DragScroll.new()
	screen_scroll = scroll
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.follow_focus = true
	body.add_child(scroll)
	var row := HBoxContainer.new()
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_theme_constant_override("separation",14)
	scroll.add_child(row)
	var result: Array = []
	for i in 2:
		var column := VBoxContainer.new()
		column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		column.add_theme_constant_override("separation",12)
		row.add_child(column)
		result.append(column)
	return result

func _setting_toggle(parent: Node, key: String, title: String, explanation: String) -> void:
	var row := _setting_row(parent,title,explanation)
	var enabled: bool = settings.get(key,true)
	_state_switch(row,key,enabled,func(): _request("settings",["--set",key+"="+str(not enabled).to_lower()]),not settings.is_empty())

func _setting_row(parent: Node, title: String, explanation: String) -> HBoxContainer:
	var row := _row(parent)
	row.add_theme_constant_override("separation",16)
	var info := VBoxContainer.new()
	info.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	info.add_theme_constant_override("separation",1)
	row.add_child(info)
	var heading := _label(title,19)
	heading.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	info.add_child(heading)
	var description := _label(explanation,15,MUTED)
	description.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	info.add_child(description)
	return row

func _state_switch(parent: Node, id: String, enabled: bool, action: Callable, available := true) -> CheckButton:
	var button := CheckButton.new()
	button.text = "On" if enabled else "Off"
	button.button_pressed = enabled
	button.custom_minimum_size = Vector2(108,42)
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	button.add_theme_font_size_override("font_size",17)
	button.disabled = not available or backend.busy
	button.set_meta("focus_id",id)
	button.tooltip_text = parent.get_child(0).get_child(0).text + (": On" if enabled else ": Off")
	var accent := MINT if enabled else MUTED
	for state in ["normal","hover","pressed","hover_pressed","disabled"]:
		button.add_theme_stylebox_override(state,_style(Color(accent.r,accent.g,accent.b,0.16 if enabled else 0.06),Color(0,0,0,0),18,12,4))
	button.add_theme_stylebox_override("focus",_style(Color(0.72,0.95,0.55,0.10),MINT,18,12,4))
	button.add_theme_color_override("font_color",accent)
	button.add_theme_color_override("font_pressed_color",accent)
	button.add_theme_color_override("font_hover_color",accent)
	button.add_theme_color_override("font_hover_pressed_color",accent)
	button.pressed.connect(action)
	button.focus_entered.connect(func(): last_focus = id)
	parent.add_child(button)
	if not button.disabled: controls.append(button)
	return button

func _check_updates() -> void:
	if not update_backend.busy: update_backend.request("update",["--detach"])

func _update_completed(_command: String, data: Dictionary) -> void:
	if data.has("error"): notice = "Update check: " + str(data.error.message)
	elif data.get("scheduled",false):
		proton_data.active = true
		notice = "Updates are running in the background. You can close DeckCompanion."
	else:
		update = data
		notice = "Update check complete."
	if tab != 0: _render()

func _proton_completed(_command: String, data: Dictionary) -> void:
	if not data.has("error") and data.has("source"):
		available_data = data
		if tab == 1: _render()
		return
	if not data.has("error") and data.get("removed") != null: notice = "Removed %s." % data.removed
	if data.has("error"):
		notice = "Proton builds: " + str(data.error.message)
		proton_data.active = false
	else:
		if data.get("scheduled",false): proton_data.merge(data,true)
		else: proton_data = data
		var job: Dictionary = data.get("job",{})
		if job.get("state") == "failed": notice = "Proton builds: " + str(job.get("error","Download failed"))
		var result: Dictionary = job.get("result",{})
		if result.has("engine"): update = result
	if tab == 1: _render()

func _install_proton(name: String) -> void:
	if not proton_backend.busy: proton_backend.request("proton",["install",name,"--detach"])

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
	var top := _row(column)
	top.add_theme_constant_override("separation",14)
	var engine := _card(top,"Engine")
	engine.get_parent().size_flags_horizontal = Control.SIZE_EXPAND_FILL
	engine.add_child(_label(str(status.get("version","Not connected")),24,INK))
	var available: String = str(update.get("engine",{}).get("available","not checked"))
	_paragraph(engine,"Available: " + available + "  ·  Steam " + ("running" if status.get("steam_running",false) else "closed"))
	var channels: Dictionary = status.get("channels",{})
	var content: Dictionary = channels.get("content",{})
	var index := _card(top,"Recipes and launchers")
	index.get_parent().size_flags_horizontal = Control.SIZE_EXPAND_FILL
	var raw_fetched: Variant = content.get("fetched_at", update.get("index",{}).get("fetched_at"))
	var fetched: String = raw_fetched if raw_fetched is String else ""
	var version: Variant = content.get("version")
	index.add_child(_label(("Content " + str(version)) if version != null else "Built-in recipes",24,INK))
	var age := "Not fetched yet"
	if not fetched.is_empty():
		var hours := maxi(0,int((Time.get_unix_time_from_system()-Time.get_unix_time_from_datetime_string(fetched))/3600))
		age = "Fetched just now" if hours == 0 else "Fetched %s hour%s ago" % [hours,"" if hours == 1 else "s"]
	_paragraph(index,age + "  ·  updates on its own, without a DeckCompanion release" + ("" if str(content.get("state","")) in ["","active","bundled"] else "  ·  " + str(content.state)))
	_chip("Checking…" if update_backend.busy else "Check for updates",_check_updates,index,"check_updates",false,MINT,not update_backend.busy)
	# Mods and runtimes follow their own upstream releases.
	var modules := _card(column,"Mods and runtimes")
	var mods: Dictionary = channels.get("mods",{})
	var resolved: Dictionary = mods.get("resolved",{}) if mods.get("resolved") is Dictionary else {}
	var mod_line := "%d mod%s tracked from their own releases" % [resolved.size(),"" if resolved.size() == 1 else "s"] if not resolved.is_empty() else "Mods resolve from their own releases on the next update check"
	if mods.get("updated_at") is String: mod_line += "  ·  checked " + str(mods.updated_at).get_slice("T",0)
	_setting_row(modules,"Mods",mod_line)
	var umu: Variant = channels.get("umu",{}).get("version")
	_setting_row(modules,"umu-launcher (non-Steam games)",("Version " + str(umu)) if umu != null else "Downloaded when you add your first non-Steam game")
	var proton := _card(column,"Proton builds")
	for build in proton_data.get("builds",[]):
		var phase: String = build.get("state","wanted")
		var message := "Installed" if phase == "installed" else phase.capitalize()
		if phase == "downloading": message = "Downloading %s %%" % build.get("percent",0)
		if phase == "failed": message = "Failed: " + str(build.get("error","Unknown error"))
		var wanted_by: Array = build.get("wanted_by",[])
		if not wanted_by.is_empty(): message += "  ·  Wanted by " + ", ".join(wanted_by)
		if build.get("latest",false): message += "  ·  Newest GE"
		if build.has("total_bytes"): message += "  ·  %.0f / %.0f MiB" % [float(build.get("downloaded_bytes",0))/1048576,float(build.total_bytes)/1048576]
		if build.has("installed_bytes"): message += "  ·  %.1f GB" % (float(build.installed_bytes)/1073741824.0)
		var row := _setting_row(proton,str(build.name),message)
		if not build.get("installed",false):
			_chip("Install",_install_proton.bind(str(build.name)),row,"proton_"+str(build.name),false,MINT,not proton_data.get("active",false) and not proton_backend.busy)
		elif build.get("removable",false):
			_chip("Remove",_confirm_proton_remove.bind(str(build.name)),row,"remove_"+str(build.name),false,WARN,not proton_data.get("active",false) and not proton_backend.busy)
	if proton_data.is_empty(): _paragraph(proton,"Reading installed and wanted builds…")
	_chip("Refresh",func(): proton_backend.request("proton"),proton,"proton_refresh",false,MINT,not proton_backend.busy)
	# More versions: any GE-Proton release, installed on demand (DC-27).
	var more := _card(column,"Get more GE-Proton versions")
	if available_data.is_empty():
		_paragraph(more,"Browse recent GE-Proton releases and install any of them. Only builds DeckCompanion can safely remove get a Remove button.")
		_chip("Show versions",func(): proton_backend.request("proton",["available"]),more,"proton_available",false,MINT,not proton_backend.busy)
	else:
		if available_data.get("stale",false) or available_data.get("warning") != null: _paragraph(more,str(available_data.get("warning","Showing the cached list")),WARN)
		for release in available_data.get("builds",[]).slice(0,8):
			var size_text: String = ("%.0f MB" % (float(release.get("size",0))/1000000.0)) if release.get("size") != null else ""
			var row := _setting_row(more,str(release.name),"Released " + str(release.get("published_at","")).get_slice("T",0) + ("  ·  " + size_text if size_text != "" else "") + ("" if release.get("digest_available",true) else "  ·  no published checksum"))
			if release.get("installed",false): _badge(row,"INSTALLED",MINT).size_flags_vertical = Control.SIZE_SHRINK_CENTER
			else: _chip("Install",_install_proton.bind(str(release.name)),row,"get_"+str(release.name),false,MINT,not proton_data.get("active",false) and not proton_backend.busy)

func _confirm_proton_remove(name: String) -> void:
	pending_proton_remove = name
	proton_remove_dialog.dialog_text = "Remove %s from this Deck?\nGames set to use it fall back to the automatic choice." % name
	modal_focus = get_viewport().gui_get_focus_owner()
	proton_remove_dialog.popup_centered()
	proton_remove_dialog.get_cancel_button().grab_focus()

func _toggle_game() -> void:
	if selected.get("kind") == "non_steam": return
	if not detail or not selected.get("has_recipe",false) or backend.busy: return
	var enabled: bool = selected.get("pending") == "enable" or (selected.get("pending") == null and selected.enabled)
	_request("disable" if enabled else "enable",[selected.appid])

func _toggle_mod(mod: Dictionary) -> void:
	_request("mod",[selected.appid,mod.id,"--off" if mod.enabled else "--on"])

func _set_filter(value: int) -> void:
	filter = value
	last_focus = "filter%s" % value
	_save_ui_config()
	_render()

func _switch_tab(value: int) -> void:
	if backend.busy: return
	tab = value
	detail = false
	add_flow = null
	last_focus = ["games","updates","settings"][value]
	if value == 2 and decky_status.is_empty(): _request_decky("status")
	if value == 1 and not proton_backend.busy: proton_backend.request("proton")
	_render()

func _back() -> void:
	if backend.busy: return
	if add_flow != null:
		add_flow.back()
		return
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
	if dialog.visible or licenses.visible or remove_dialog.visible or restore_dialog.visible or proton_remove_dialog.visible or decky_dialog.visible or backend.busy: return
	if event.is_action_pressed("ui_cancel"): _back()
	elif event.is_action_pressed("dc_left") or event.is_action_pressed("dc_right"): _switch_tab(Nav.next_index(tab,-1 if event.is_action_pressed("dc_left") else 1,3))
	elif event.is_action_pressed("dc_x"):
		if detail: _toggle_game()
		elif tab == 0: _set_filter(Nav.next_index(filter,1,FILTERS.size()))
	elif event.is_action_pressed("dc_y"): _confirm()
	else: return
	get_viewport().set_input_as_handled()

func _process(delta: float) -> void:
	launcher_poll += delta
	if launcher_poll >= 1.5 and add_flow != null and launchers_busy() and not launcher_backend.busy:
		launcher_poll = 0.0
		launcher_backend.request("launchers")
	proton_poll += delta
	if proton_poll >= 1.0 and proton_data.get("active",false) and not proton_backend.busy:
		proton_poll = 0.0
		proton_backend.request("proton")
	var scroll: ScrollContainer = license_scroll if licenses.visible else (screen_scroll if add_flow != null else (log_view if detail else (screen_scroll if tab != 0 else preview_scroll)))
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
	for screen in ["games","detail","updates","settings","add"]:
		if screen == "games":
			await get_tree().create_timer(0.6).timeout
			for control in controls:
				if control.get_meta("focus_id","") == games[0].appid: control.grab_focus()
		if screen == "detail": _open_game(games[0])
		if screen == "updates":
			_switch_tab(1)
			_request("update")
		if screen == "settings": _switch_tab(2)
		if screen == "add":
			_switch_tab(0)
			add_flow = AddGame.new(self)
			launcher_backend.request("launchers")
			_render()
		await get_tree().create_timer(1.0).timeout
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(directory.path_join(screen+".png"))
	get_tree().quit()
