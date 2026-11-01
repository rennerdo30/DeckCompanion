extends Control
# Route before constructing the management UI or any engine backend.
func _ready() -> void:
	var args := OS.get_cmdline_args() + OS.get_cmdline_user_args()
	var index := args.find("--splash")
	if index >= 0:
		if index + 1 >= args.size():
			get_tree().quit(1)
			return
		var splash: Control = load("res://scripts/splash.gd").new()
		splash.file = args[index + 1]
		add_child(splash)
	else:
		add_child(load("res://main.tscn").instantiate())
