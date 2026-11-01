extends SceneTree
# Reproducible original fixture images. No downloaded art or game logos.
func _initialize() -> void:
	print("Fixture art process PID: ", OS.get_process_id())
	DirAccess.make_dir_recursive_absolute("res://fixtures/art")
	var scenes := {
		"fields": '<rect width="960" height="300" fill="#143d49"/><circle cx="735" cy="90" r="52" fill="#e5d7a1"/><path d="M0 205L260 90L560 200L800 120L960 200V300H0" fill="#3e7468"/><path d="M0 240Q380 110 960 230V300H0" fill="#8ca168"/><path d="M0 300L420 200M240 300L490 202M520 300L560 204M800 300L630 208M960 270L700 212" stroke="#c7cc8e" stroke-width="4"/><rect x="150" y="168" width="90" height="44" rx="5" fill="#d4e5c9"/><rect x="180" y="141" width="35" height="32" rx="3" fill="#203e42"/><circle cx="172" cy="211" r="21" fill="#16313a"/><circle cx="225" cy="211" r="16" fill="#16313a"/>',
		"orbit": '<rect width="960" height="300" fill="#12293f"/><circle cx="670" cy="150" r="106" fill="#6b9382"/><ellipse cx="670" cy="150" rx="210" ry="48" fill="none" stroke="#c5d49b" stroke-width="9" transform="rotate(-20 670 150)"/><circle cx="200" cy="90" r="5" fill="#dfebce"/><circle cx="380" cy="220" r="3" fill="#dfebce"/><circle cx="100" cy="240" r="3" fill="#dfebce"/><circle cx="440" cy="45" r="4" fill="#dfebce"/>',
		"courier": '<rect width="960" height="300" fill="#242c42"/><circle cx="690" cy="115" r="72" fill="#b6c58d"/><path d="M0 300V130H90V180H170V80H240V160H300V110H380V220H440V140H520V190H600V300" fill="#3e626a"/><path d="M0 300V240H150V200H230V260H330V170H420V240H510V210H590V130H670V220H780V170H880V240H960V300" fill="#183a46"/><path d="M0 280H960M195 220V250M365 190V225M625 155V192M815 193V222" stroke="#a3ddaf" stroke-width="5"/>'
	}
	for key in scenes:
		var svg := '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="300" viewBox="0 0 960 300">' + str(scenes[key]) + '</svg>'
		var image := Image.new()
		if image.load_svg_from_string(svg) != OK or image.save_png("res://fixtures/art/" + key + ".png") != OK:
			quit(1)
			return
		# Runtime decoding needs source PNG bytes, including inside exported PCKs.
		var config := ConfigFile.new()
		config.set_value("remap", "importer", "keep")
		config.save("res://fixtures/art/" + key + ".png.import")
	quit(0)
