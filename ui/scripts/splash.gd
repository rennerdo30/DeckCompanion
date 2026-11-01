extends Control
signal finished
const Cache = preload("res://scripts/art_cache.gd")
const Art = preload("res://scripts/game_art.gd")
const Text = preload("res://scripts/game_text.gd")
const DragScroll = preload("res://scripts/drag_scroll.gd")
const INK := Color("e7f1f4")
const MINT := Color("b8f18b")
const MUTED := Color("a9bcc6")
const CREAM := Color("e9d9a0")
var file := ""
var auto_quit := true
var done := false
var loaded := false
var elapsed := 0.0
var duration := 4.0
const HOLD_CAP := 120.0
var held := false
var held_elapsed := 0.0
var skip_button: Button
var hold_button: Button
var countdown: Label
var footer_note: Label
var cache: Node
var art: Control
var logo: TextureRect
var title: Label
var proton_line: Label
var fixes_line: Label
var mod_rows: VBoxContainer
var warnings: Label
var progress: ProgressBar
var content: VBoxContainer
var hero: PanelContainer
var setup_panel: PanelContainer
var columns: HBoxContainer
var payload: Dictionary = {}
var status_line: Label
var chips: HFlowContainer
var parallel := false
var skip_tracking := false

func label(text: String, size_px: int, color := INK) -> Label:
	var node := Label.new()
	node.text = text
	node.add_theme_font_size_override("font_size",size_px)
	node.add_theme_color_override("font_color",color)
	node.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return node

func _ready() -> void:
	# Follow the window: anchors at the edges with zero offsets (also when docked at 1080p).
	set_anchors_preset(Control.PRESET_FULL_RECT)
	offset_left = 0
	offset_top = 0
	offset_right = 0
	offset_bottom = 0
	var capture: bool = Array(OS.get_cmdline_user_args()).any(func(arg): return arg.begins_with("--splash-capture="))
	# gamescope already presents every window fullscreen. Switching the window mode after
	# the first frame made the Deck show black for a moment (owner report, DC-26), so only
	# switch outside gamescope (desktop sessions).
	var under_gamescope := OS.get_environment("XDG_CURRENT_DESKTOP").to_lower().contains("gamescope") or OS.has_environment("GAMESCOPE_WAYLAND_DISPLAY") or OS.get_environment("SteamDeck") == "1"
	if auto_quit and not capture and DisplayServer.get_name() != "headless" and not under_gamescope:
		DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_FULLSCREEN)
	cache = Cache.new()
	add_child(cache)
	# Full-bleed key visual: the game's artwork behind everything.
	hero = PanelContainer.new()
	hero.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	hero.clip_contents = true
	hero.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var flat := StyleBoxFlat.new()
	flat.bg_color = Color("0a1218")
	hero.add_theme_stylebox_override("panel",flat)
	add_child(hero)
	art = Art.new()
	art.cache = cache
	hero.add_child(art)
	for spec in [[Vector2(0,0),Vector2(1,0),[Color("0a1218f0"),Color("0a121840")]],[Vector2(0,1),Vector2(0,0.35),[Color("0a1218fa"),Color("0a121800")]]]:
		var shade := TextureRect.new()
		var gradient := Gradient.new()
		gradient.colors = PackedColorArray(spec[2])
		var texture := GradientTexture2D.new()
		texture.gradient = gradient
		texture.fill_from = spec[0]
		texture.fill_to = spec[1]
		shade.texture = texture
		shade.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		shade.stretch_mode = TextureRect.STRETCH_SCALE
		shade.mouse_filter = Control.MOUSE_FILTER_IGNORE
		hero.add_child(shade)
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left","right"]: margin.add_theme_constant_override("margin_"+side,44)
	margin.add_theme_constant_override("margin_top",32)
	margin.add_theme_constant_override("margin_bottom",28)
	add_child(margin)
	content = VBoxContainer.new()
	content.add_theme_constant_override("separation",12)
	margin.add_child(content)
	var brand := HBoxContainer.new()
	brand.add_theme_constant_override("separation",12)
	content.add_child(brand)
	logo = TextureRect.new()
	logo.custom_minimum_size = Vector2(40,40)
	logo.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	logo.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	brand.add_child(logo)
	var brand_name := label("DeckCompanion",22,INK)
	brand_name.autowrap_mode = TextServer.AUTOWRAP_OFF
	brand_name.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	brand.add_child(brand_name)
	var push := Control.new()
	push.size_flags_vertical = Control.SIZE_EXPAND_FILL
	push.mouse_filter = Control.MOUSE_FILTER_IGNORE
	content.add_child(push)
	content.add_child(label("LAUNCHING WITH DECKCOMPANION",16,MINT))
	title = label("Getting your game ready",54)
	title.max_lines_visible = 2
	title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	title.add_theme_color_override("font_shadow_color",Color(0,0,0,0.55))
	title.add_theme_constant_override("shadow_offset_y",3)
	content.add_child(title)
	status_line = label("Your game is starting.",20,MUTED)
	content.add_child(status_line)
	chips = HFlowContainer.new()
	chips.add_theme_constant_override("h_separation",8)
	chips.add_theme_constant_override("v_separation",8)
	content.add_child(chips)
	setup_panel = PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = Color("0e1c25d9")
	box.set_corner_radius_all(14)
	box.set_content_margin_all(18)
	setup_panel.add_theme_stylebox_override("panel",box)
	setup_panel.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	setup_panel.custom_minimum_size.x = 760
	content.add_child(setup_panel)
	var scroll := DragScroll.new()
	scroll.scroll_started.connect(func(): skip_tracking = false)
	scroll.custom_minimum_size.y = 132
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	setup_panel.add_child(scroll)
	columns = HBoxContainer.new()
	columns.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	columns.add_theme_constant_override("separation",28)
	scroll.add_child(columns)
	var setup := VBoxContainer.new()
	setup.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	setup.add_theme_constant_override("separation",6)
	columns.add_child(setup)
	setup.add_child(label("FIXES",15,MINT))
	proton_line = label("",19)
	setup.add_child(proton_line)
	fixes_line = label("",17,INK)
	setup.add_child(fixes_line)
	mod_rows = VBoxContainer.new()
	mod_rows.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	mod_rows.add_theme_constant_override("separation",6)
	columns.add_child(mod_rows)
	warnings = label("",17,Color("ffcf9d"))
	content.add_child(warnings)
	progress = ProgressBar.new()
	progress.custom_minimum_size.y = 6
	progress.show_percentage = false
	for spec in [["background",Color("ffffff22")],["fill",MINT]]:
		var style := StyleBoxFlat.new()
		style.bg_color = spec[1]
		style.set_corner_radius_all(3)
		progress.add_theme_stylebox_override(spec[0],style)
	content.add_child(progress)
	var footer := HBoxContainer.new()
	footer.add_theme_constant_override("separation",14)
	content.add_child(footer)
	countdown = label("",17,INK)
	countdown.autowrap_mode = TextServer.AUTOWRAP_OFF
	countdown.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	footer.add_child(countdown)
	footer_note = label("B also skips",16,MUTED)
	footer_note.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	footer_note.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	footer.add_child(footer_note)
	skip_button = action_button("A","Skip",finish)
	footer.add_child(skip_button)
	hold_button = action_button("Y","Hold",hold)
	footer.add_child(hold_button)
	skip_button.grab_focus.call_deferred()
	# Present the generated fallback first. Disk/texture loading starts after a frame.
	_read_after_frame()

func _chip(text: String, color := INK) -> void:
	var chip := label(text,16,color)
	chip.autowrap_mode = TextServer.AUTOWRAP_OFF
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.06,0.11,0.14,0.85)
	style.border_color = Color(color.r,color.g,color.b,0.45)
	style.set_border_width_all(1)
	style.set_corner_radius_all(14)
	style.content_margin_left = 12
	style.content_margin_right = 12
	style.content_margin_top = 4
	style.content_margin_bottom = 4
	chip.add_theme_stylebox_override("normal",style)
	chips.add_child(chip)

func _read_after_frame() -> void:
	await get_tree().process_frame
	if file.is_empty(): return
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(file))
	if not parsed is Dictionary or not parsed.get("name") is String:
		printerr("Invalid DeckCompanion splash JSON")
		if auto_quit: get_tree().quit(1)
		return
	set_payload(parsed)
	ResourceLoader.load_threaded_request("res://icon.svg")
	_capture_if_requested()

func set_payload(data: Dictionary) -> void:
	payload = data
	parallel = data.get("parallel",false) == true
	duration = clampf(float(data.get("duration_seconds",4)),1,10)
	elapsed = 0
	held = false
	held_elapsed = 0
	done = false
	progress.value = 0
	skip_button.text = "Close" if parallel else "Skip"
	hold_button.show()
	footer_note.text = "B also closes" if parallel else "B also skips"
	status_line.text = "Your game is loading in the background." if parallel else "Your game starts when the bar is full."
	loaded = true
	title.text = data.get("name", "Game")
	art.show_game(data)
	for child in chips.get_children(): child.queue_free()
	proton_line.text = Text.proton(data)
	_chip(Text.proton(data),INK)
	var fixes: Dictionary = data.get("fixes",{})
	if data.get("recipe_version") != null: _chip("Recipe " + str(data.recipe_version),MINT)
	if fixes.get("upstream_chained",false): _chip("Community fixes ✓",MINT)
	var steps := int(fixes.get("wine_steps_count",0))
	if steps > 0: _chip("%d DeckCompanion fix%s" % [steps,"" if steps == 1 else "es"],MINT)
	var mod_count: int = data.get("mods",[]).filter(func(m): return m.get("enabled",false)).size()
	if mod_count > 0: _chip("%d mod%s" % [mod_count,"" if mod_count == 1 else "s"],CREAM)
	# The chips already summarise Proton and recipe; the card lists what actually runs.
	proton_line.visible = false
	fixes_line.text = "Community fixes (umu-protonfixes): " + ("on" if fixes.get("upstream_chained",false) else "off")
	for step in fixes.get("wine_steps",[]): fixes_line.text += "\n" + str(step)
	for child in mod_rows.get_children(): child.queue_free()
	mod_rows.add_child(label("MODS",15,MINT))
	for mod in data.get("mods",[]):
		mod_rows.add_child(label(Text.mod_label(mod),17))
		if mod.get("state_message", "") != "": mod_rows.add_child(label(mod.state_message,15,MUTED))
	if data.get("mods",[]).is_empty(): mod_rows.add_child(label("No optional mods",17,MUTED))
	warnings.text = "\n".join(data.get("warnings",[]))
	warnings.max_lines_visible = 2
	warnings.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	warnings.visible = not warnings.text.is_empty()

func _process(delta: float) -> void:
	if not loaded or done: return
	if logo.texture == null and ResourceLoader.load_threaded_get_status("res://icon.svg") == ResourceLoader.THREAD_LOAD_LOADED:
		logo.texture = ResourceLoader.load_threaded_get("res://icon.svg")
	if held:
		held_elapsed += delta
		if held_elapsed >= HOLD_CAP: finish()
		return
	elapsed += delta
	progress.value = minf(100,elapsed / duration * 100)
	countdown.text = ("Closing in %.1f s" if parallel else "Starting in %.1f s") % maxf(0,duration-elapsed)
	if elapsed >= duration: finish()

func action_button(glyph: String, caption: String, action: Callable) -> Button:
	var button := Button.new()
	button.text = caption
	button.custom_minimum_size = Vector2(160,48)
	button.alignment = HORIZONTAL_ALIGNMENT_RIGHT
	button.add_theme_font_size_override("font_size",20)
	for state in ["normal","hover","pressed","focus"]:
		var style := StyleBoxFlat.new()
		style.bg_color = Color("233e36") if state != "hover" else Color("345a45")
		style.border_color = MINT if state == "focus" else Color("4a6a5a")
		style.set_border_width_all(2 if state == "focus" else 1)
		style.set_corner_radius_all(12)
		style.content_margin_left = 58
		style.content_margin_right = 20
		button.add_theme_stylebox_override(state,style)
	button.pressed.connect(action)
	var chip := PanelContainer.new()
	chip.position = Vector2(16,11)
	chip.size = Vector2(30,30)
	chip.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var circle := StyleBoxFlat.new()
	circle.bg_color = Color("142a24")
	circle.border_color = MINT
	circle.set_border_width_all(1)
	circle.set_corner_radius_all(15)
	chip.add_theme_stylebox_override("panel",circle)
	button.add_child(chip)
	var letter := label(glyph,18,MINT)
	letter.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	letter.mouse_filter = Control.MOUSE_FILTER_IGNORE
	chip.add_child(letter)
	return button

func hold() -> void:
	if done or held: return
	held = true
	countdown.text = "Paused"
	skip_button.text = "Continue"
	hold_button.hide()
	footer_note.text = "Take your time · B also continues"
	skip_button.grab_focus()

func _input(event: InputEvent) -> void:
	if (event is InputEventJoypadButton and event.pressed and event.button_index == JOY_BUTTON_Y) or (event is InputEventKey and event.pressed and not event.echo and event.physical_keycode == KEY_Y):
		hold()
		get_viewport().set_input_as_handled()
		return
	var skip: bool = event is InputEventJoypadButton and event.pressed and event.button_index in [JOY_BUTTON_A,JOY_BUTTON_B]
	if event is InputEventScreenTouch or (event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT):
		var over_button: bool = skip_button.get_global_rect().has_point(event.position) or (hold_button.visible and hold_button.get_global_rect().has_point(event.position))
		if event.pressed: skip_tracking = not held and not over_button
		else:
			skip = skip_tracking and not over_button
			skip_tracking = false
	skip = skip or event.is_action_pressed("ui_accept") or event.is_action_pressed("ui_cancel")
	if skip:
		finish()
		get_viewport().set_input_as_handled()

func finish() -> void:
	if done: return
	done = true
	finished.emit()
	if auto_quit: get_tree().quit(0)

func _capture_if_requested() -> void:
	for arg in OS.get_cmdline_user_args():
		if not arg.begins_with("--splash-capture="): continue
		await get_tree().create_timer(0.6).timeout
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(arg.trim_prefix("--splash-capture="))
