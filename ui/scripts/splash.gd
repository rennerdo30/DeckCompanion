extends Control
signal finished
const Cache = preload("res://scripts/art_cache.gd")
const Art = preload("res://scripts/game_art.gd")
const Text = preload("res://scripts/game_text.gd")
const DragScroll = preload("res://scripts/drag_scroll.gd")
const INK := Color("e7f1f4")
const MINT := Color("b8f18b")
const MUTED := Color("9ab0bc")
var file := ""
var auto_quit := true
var done := false
var loaded := false
var elapsed := 0.0
var duration := 2.5
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
var payload: Dictionary = {}
var skip_tracking := false

func label(text: String, size_px: int, color := INK) -> Label:
	var node := Label.new()
	node.text = text
	node.add_theme_font_size_override("font_size",size_px)
	node.add_theme_color_override("font_color",color)
	node.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return node

func _ready() -> void:
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	var capture: bool = Array(OS.get_cmdline_user_args()).any(func(arg): return arg.begins_with("--splash-capture="))
	if auto_quit and not capture and DisplayServer.get_name() != "headless":
		DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_FULLSCREEN)
	cache = Cache.new()
	add_child(cache)
	art = Art.new()
	art.cache = cache
	art.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(art)
	var shade := TextureRect.new()
	var gradient := Gradient.new()
	gradient.colors = PackedColorArray([Color("09141bf5"),Color("09141ba0")])
	var texture := GradientTexture2D.new()
	texture.gradient = gradient
	texture.fill_from = Vector2(0,1)
	texture.fill_to = Vector2(1,0)
	shade.texture = texture
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(shade)
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left","right","top","bottom"]: margin.add_theme_constant_override("margin_"+side,48)
	add_child(margin)
	content = VBoxContainer.new()
	content.add_theme_constant_override("separation",18)
	margin.add_child(content)
	var brand := HBoxContainer.new()
	brand.add_theme_constant_override("separation",16)
	content.add_child(brand)
	logo = TextureRect.new()
	logo.custom_minimum_size = Vector2(64,64)
	logo.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	logo.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	brand.add_child(logo)
	var brand_name := label("DECKCOMPANION",24,MINT)
	brand_name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	brand.add_child(brand_name)
	var spacer := Control.new()
	spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	content.add_child(spacer)
	content.add_child(label("LAUNCHING WITH DECKCOMPANION",17,MINT))
	title = label("Getting your game ready",48)
	title.max_lines_visible = 2
	title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	content.add_child(title)
	var panel := PanelContainer.new()
	var box := StyleBoxFlat.new()
	box.bg_color = Color("10232eeb")
	box.set_corner_radius_all(16)
	box.set_content_margin_all(24)
	panel.add_theme_stylebox_override("panel",box)
	content.add_child(panel)
	var scroll := DragScroll.new()
	scroll.scroll_started.connect(func(): skip_tracking = false)
	scroll.custom_minimum_size.y = 240
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel.add_child(scroll)
	var columns := HBoxContainer.new()
	columns.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	columns.add_theme_constant_override("separation",32)
	scroll.add_child(columns)
	var setup := VBoxContainer.new()
	setup.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	setup.add_theme_constant_override("separation",12)
	columns.add_child(setup)
	setup.add_child(label("YOUR LAUNCH SETUP",15,MINT))
	proton_line = label("",23)
	setup.add_child(proton_line)
	fixes_line = label("",19,MUTED)
	setup.add_child(fixes_line)
	mod_rows = VBoxContainer.new()
	mod_rows.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	mod_rows.add_theme_constant_override("separation",10)
	columns.add_child(mod_rows)
	warnings = label("",18,Color("ffcf9d"))
	content.add_child(warnings)
	progress = ProgressBar.new()
	progress.custom_minimum_size.y = 5
	progress.show_percentage = false
	for spec in [["background",Color("263c45")],["fill",MINT]]:
		var style := StyleBoxFlat.new()
		style.bg_color = spec[1]
		style.set_corner_radius_all(3)
		progress.add_theme_stylebox_override(spec[0],style)
	content.add_child(progress)
	content.add_child(label("A / B  or touch to skip                                      Your game starts automatically",17,MUTED))
	# Present the generated fallback first. Disk/texture loading starts after a frame.
	_read_after_frame()

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
	duration = clampf(float(data.get("duration_seconds",2.5)),1,6)
	elapsed = 0
	loaded = true
	title.text = data.get("name", "Game")
	art.show_game(data)
	proton_line.text = Text.proton(data)
	var fixes: Dictionary = data.get("fixes",{})
	fixes_line.text = "Recipe %s\nUpstream chaining: %s · Our Wine steps: %s" % [data.get("recipe_version") if data.get("recipe_version") != null else "not needed", "yes" if fixes.get("upstream_chained",false) else "no", int(fixes.get("wine_steps_count",0))]
	for step in fixes.get("wine_steps",[]): fixes_line.text += "\n" + str(step)
	for child in mod_rows.get_children(): child.queue_free()
	mod_rows.add_child(label("MODS & CONTENT",15,MINT))
	for mod in data.get("mods",[]):
		mod_rows.add_child(label(Text.mod_label(mod),20))
		if mod.get("state_message", "") != "": mod_rows.add_child(label(mod.state_message,17,MUTED))
	if data.get("mods",[]).is_empty(): mod_rows.add_child(label("No optional mods",20,MUTED))
	warnings.text = "\n".join(data.get("warnings",[]))
	warnings.max_lines_visible = 2
	warnings.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	warnings.visible = not warnings.text.is_empty()

func _process(delta: float) -> void:
	if not loaded or done: return
	if logo.texture == null and ResourceLoader.load_threaded_get_status("res://icon.svg") == ResourceLoader.THREAD_LOAD_LOADED:
		logo.texture = ResourceLoader.load_threaded_get("res://icon.svg")
	elapsed += delta
	progress.value = minf(100,elapsed / duration * 100)
	if elapsed >= duration: finish()

func _input(event: InputEvent) -> void:
	var skip: bool = event is InputEventJoypadButton and event.pressed and event.button_index in [JOY_BUTTON_A,JOY_BUTTON_B]
	if event is InputEventScreenTouch or (event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT):
		if event.pressed: skip_tracking = true
		else:
			skip = skip_tracking
			skip_tracking = false
	skip = skip or event.is_action_pressed("ui_accept") or event.is_action_pressed("ui_cancel")
	if skip: finish()

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
