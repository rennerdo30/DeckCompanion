extends SceneTree
# Offscreen mock captures. Pass --mock --polish-capture-dir=<path>; no engine/device calls.
var directory := ""
var suffix := ""

func _initialize() -> void:
	call_deferred("run")

func capture(name: String) -> void:
	await create_timer(0.5).timeout
	await RenderingServer.frame_post_draw
	var result := root.get_texture().get_image().save_png(directory.path_join(name+suffix+".png"))
	if result != OK:
		printerr("Capture failed: ",result)
		quit(1)

func run() -> void:
	print("Capture process PID: ",OS.get_process_id())
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--polish-capture-dir="): directory = arg.trim_prefix("--polish-capture-dir=")
	if directory.is_empty() or not "--mock" in OS.get_cmdline_user_args():
		quit(1)
		return
	DirAccess.make_dir_recursive_absolute(directory)
	# Render the layout at native pixels at both requested resolutions.
	root.content_scale_size = DisplayServer.window_get_size()
	if root.content_scale_size.x > 1280: suffix = "-1920x1080"
	var ui: Node = load("res://main.tscn").instantiate()
	root.add_child(ui)
	await create_timer(1).timeout
	ui._switch_tab(2)
	await capture("settings")
	ui.screen_scroll.follow_focus = false
	if root.gui_get_focus_owner() != null: root.gui_get_focus_owner().release_focus()
	ui.screen_scroll.scroll_vertical = 100000
	await capture("settings-about")
	ui.queue_free()
	await process_frame
	var splash: Control = load("res://scripts/splash.gd").new()
	splash.auto_quit = false
	root.add_child(splash)
	splash.set_payload(JSON.parse_string(FileAccess.get_file_as_string("res://fixtures/splash.json")))
	ResourceLoader.load_threaded_request("res://icon.svg")
	await capture("splash")
	splash.hold()
	await capture("splash-held")
	splash.queue_free()
	await process_frame
	quit()
