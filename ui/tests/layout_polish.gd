extends RefCounted
# Geometric checks exercise the live container layout, including long content.
func labels(node: Node, text: String) -> Array:
	var found := []
	if node is Label and node.text == text: found.append(node)
	for child in node.get_children(): found.append_array(labels(child,text))
	return found

func scrolls(node: Node) -> Array:
	var found := []
	if node is ScrollContainer: found.append(node)
	for child in node.get_children(): found.append_array(scrolls(child))
	return found

func focus_node(node: Node, id: String) -> Control:
	if node.get_meta("focus_id","") == id: return node
	for child in node.get_children():
		var found := focus_node(child,id)
		if found != null: return found
	return null

func run(test: SceneTree, ui: Node) -> void:
	ui._switch_tab(2)
	await test.create_timer(0.15).timeout
	for spec in [["splash_toggle","Launch splash"],["default","Default for all games"],
		["show_games_without_recipe","Show games without a recipe"],
		["check_updates_on_start","Check when DeckCompanion opens"]]:
		var button: Control = test.control(ui,spec[0])
		test.check(button is CheckButton,"setting exposes an actual toggle: " + spec[0])
		if button == null: continue
		test.check(button.size.x <= 180,"setting state is a compact control: " + spec[0])
		var headings := labels(ui.body,spec[1])
		test.check(headings.size() == 1,"each setting has its own title: " + spec[0])
		if headings.size() != 1: continue
		var heading: Label = headings[0]
		var description: Label = heading.get_parent().get_child(1)
		test.check(description.position.y >= heading.position.y + heading.size.y and
			description.get_parent() == heading.get_parent(),"explanation directly follows its title: " + spec[0])
		var row: Control = heading.get_parent().get_parent()
		test.check(row.get_global_rect().encloses(button.get_global_rect()),"toggle shares its setting row: " + spec[0])
	var durations := labels(ui.body,"Splash duration")
	test.check(durations.size() == 1,"duration stepper has a visible setting title")
	if durations.size() == 1:
		var row: Control = durations[0].get_parent().get_parent()
		test.check(row.is_ancestor_of(focus_node(ui.body,"splash_less")) and
			row.is_ancestor_of(focus_node(ui.body,"splash_more")),"stepper and duration title stay in one row")
	for viewport in [Vector2i(1280,800),Vector2i(1920,1080)]:
		test.root.size = viewport
		await test.create_timer(0.15).timeout
		test.check(ui.screen_scroll.size.x <= viewport.x and ui.screen_scroll.get_global_rect().end.y <= viewport.y,
			"Settings scroll stays inside viewport " + str(viewport))
	var splash: Control = load("res://scripts/splash.gd").new()
	splash.auto_quit = false
	test.root.add_child(splash)
	var payload: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://fixtures/splash.json"))
	payload.name = "A long game title with a complete edition and an extended subtitle"
	payload.duration_seconds = 6
	for i in 12: payload.mods.append(payload.mods[0].duplicate(true))
	for viewport in [Vector2i(1280,800),Vector2i(1920,1080)]:
		test.root.size = viewport
		splash.set_payload(payload)
		await test.create_timer(0.25).timeout
		var art_rect: Rect2 = splash.art.get_global_rect()
		var visible_size: Vector2 = splash.get_viewport_rect().size  # canvas_items stretch keeps a 1280x800 logical canvas
		test.check(art_rect.size.x >= visible_size.x - 1 and art_rect.size.y >= visible_size.y - 1,
			"splash artwork is a full-bleed key visual " + str(viewport))
		test.check(splash.content.get_global_rect().end.y <= viewport.y,
			"long title and many mods keep footer inside splash " + str(viewport))
		var containers := scrolls(splash)
		test.check(not containers.is_empty() and containers[0].get_v_scroll_bar().max_value > containers[0].size.y,
			"long splash content remains scrollable " + str(viewport))
	splash.queue_free()
	test.root.size = Vector2i(1280,800)
	ui._switch_tab(0)
	await test.create_timer(0.15).timeout
