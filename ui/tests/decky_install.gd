extends SceneTree
var failures := 0

func _initialize() -> void:
	call_deferred("run")

func check(value: bool, label: String) -> void:
	if not value:
		failures += 1
		printerr("FAIL: ",label)

func capture(name: String) -> void:
	if DisplayServer.get_name() == "headless": return
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--decky-captures="):
			var directory := arg.trim_prefix("--decky-captures=")
			DirAccess.make_dir_recursive_absolute(directory)
			await RenderingServer.frame_post_draw
			get_root().get_texture().get_image().save_png(directory.path_join(name+".png"))

func run() -> void:
	print("Decky test PID: ",OS.get_process_id())
	var ui = load("res://main.tscn").instantiate()
	get_root().add_child(ui)
	await create_timer(0.8).timeout
	ui._switch_tab(2)
	await create_timer(0.6).timeout
	check(ui.controls.any(func(c): return c.get_meta("focus_id","") == "plugin_install"),"root-owned bundle offers Install")
	ui.decky_backend.set_meta("action","plugin-install")
	ui._decky_completed("decky",{"method":"decky_api","requested":true,"confirm_in_steam":true,"installed":false,"changed":false,"reason":"confirmation_pending"})
	await create_timer(0.3).timeout
	check(ui.notice.contains("confirm") and not ui.notice.contains("up to date"),"pending request directs user to Steam")
	await capture("confirmation")
	ui.decky_backend.set_meta("action","plugin-install")
	ui._decky_completed("decky",{"method":"decky_api","requested":true,"confirm_in_steam":true,"installed":true,"version":"0.0.9","changed":false,"reason":"confirmation_pending"})
	await create_timer(0.3).timeout
	check(ui.notice.contains("confirm"),"pending update also directs user to Steam")
	ui.decky_status["password_set"] = true
	ui.decky_backend.set_meta("action","plugin-install")
	ui._decky_completed("decky",{"requested":false,"reason":"decky_api_unavailable"})
	check(ui.decky_action == "plugin-install" and ui.decky_dialog.visible,"API unavailable opens password fallback")
	check(ui.decky_dialog.get_meta("heading").text == "Install DeckCompanion plugin","fallback names plugin rather than Loader")
	check(not ui.decky_fields[1].visible,"existing password needs one field")
	await process_frame
	await capture("password")
	ui.decky_dialog.hide()
	ui.decky_backend.set_meta("action","plugin-install")
	ui._decky_completed("decky",{"method":"password","installed":true,"changed":true,"loader_restarted":true})
	await create_timer(0.3).timeout
	check(ui.notice == "DeckCompanion plugin installed.","confirmed install reports success")
	print("Decky focused UI checks: ",failures," failures")
	quit(1 if failures else 0)
