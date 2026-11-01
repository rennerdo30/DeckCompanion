extends SceneTree
func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var dir := args[0]
	for name in ["hero", "logo", "wide", "portrait", "icon"]:
		var img := Image.new()
		var err := img.load_svg_from_string(FileAccess.get_file_as_string(dir + "/" + name + ".svg"), 0.5 if name == "icon" else 1.0)
		img.save_png(dir + "/" + name + ".png")
		print(name, " err=", err, " ", img.get_width(), "x", img.get_height())
	quit()
