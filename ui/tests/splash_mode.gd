extends SceneTree
# Exercises the real boot route with --splash, without mock backend assistance.
func _initialize() -> void:
	call_deferred("run")

func run() -> void:
	print("Splash mode process PID: ",OS.get_process_id())
	var boot: Node = load("res://boot.tscn").instantiate()
	root.add_child(boot)
	await create_timer(0.25).timeout
	var splash: Node = boot.get_child(0)
	if splash.get_script().resource_path != "res://scripts/splash.gd" or splash.get("loaded") != true:
		printerr("FAIL: boot did not route directly to splash")
		quit(1)
		return
	print("PASS: --splash routes before management UI/engine backend")
	# Leave duration/exit behavior intact; the process must finish itself with code 0.
