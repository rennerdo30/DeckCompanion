extends RefCounted

func button(node: Node, caption: String) -> Button:
	if node is Button and node.text == caption: return node
	for child in node.get_children():
		var found := button(child,caption)
		if found != null: return found
	return null

func press(splash: Node, key: JoyButton) -> void:
	var event := InputEventJoypadButton.new()
	event.device = 7
	event.button_index = key
	event.pressed = true
	splash._input(event)

func run(test: SceneTree) -> void:
	var splash: Control = load("res://scripts/splash.gd").new()
	splash.auto_quit = false
	test.root.add_child(splash)
	splash.set_payload({"name":"Hold fixture"})
	# Disable automatic frame ticking; exercise deterministic virtual time.
	splash.set_process(false)
	test.check(splash.duration == 4,"splash defaults to four seconds")
	await test.process_frame
	splash._process(0.5)
	var progress: float = splash.progress.value
	press(splash,JOY_BUTTON_Y)
	splash._process(10)
	test.check(not splash.done and splash.elapsed == 0.5 and splash.progress.value == progress,
		"Y holds countdown and progress from any controller device")
	var paused := false
	for label in splash.find_children("*","Label",true,false):
		if label.text == "Paused": paused = true
	test.check(paused,"held progress explicitly says Paused")
	var continue_button := button(splash,"Continue")
	test.check(continue_button != null,"held splash presents clickable A Continue")
	if continue_button != null:
		continue_button.pressed.emit()
		test.check(splash.done,"Continue button starts the game now")
	splash.set_payload({"name":"Safety fixture","duration_seconds":10})
	test.check(splash.duration == 10,"splash accepts ten-second duration")
	press(splash,JOY_BUTTON_Y)
	splash._process(119.9)
	test.check(not splash.done,"held splash stays open until the safety cap")
	splash._process(0.2)
	test.check(splash.done,"held splash starts automatically after 120 seconds")
	splash.set_payload({"name":"Click fixture"})
	var hold_button := button(splash,"Hold")
	test.check(hold_button != null and button(splash,"Skip") != null,"normal splash has visible Skip and Hold buttons")
	if hold_button != null:
		# A touch targeted at Hold must reach its button instead of the global skip handler.
		var touch := InputEventScreenTouch.new()
		touch.position = hold_button.get_global_rect().get_center()
		touch.pressed = true
		splash._input(touch)
		touch.pressed = false
		splash._input(touch)
		test.check(not splash.done,"touching Hold does not trigger global skip")
		hold_button.pressed.emit()
		splash._process(8)
		test.check(not splash.done and splash.elapsed == 0,"clickable Hold pauses countdown")
		press(splash,JOY_BUTTON_B)
		test.check(splash.done,"B starts game while held")
	splash.set_payload({"name":"Mouse fixture"})
	await test.process_frame
	hold_button = button(splash,"Hold")
	if hold_button != null:
		var mouse := InputEventMouseButton.new()
		mouse.button_index = MOUSE_BUTTON_LEFT
		mouse.position = hold_button.get_global_rect().get_center()
		mouse.pressed = true
		test.root.push_input(mouse,true)
		mouse.pressed = false
		test.root.push_input(mouse,true)
		await test.process_frame
		test.check(splash.get("held") == true and not splash.done,"real mouse event route activates Hold")
		continue_button = button(splash,"Continue")
		if continue_button != null:
			mouse.position = continue_button.get_global_rect().get_center()
			mouse.pressed = true
			test.root.push_input(mouse,true)
			mouse.pressed = false
			test.root.push_input(mouse,true)
			await test.process_frame
			test.check(splash.done,"real mouse event route activates Continue")
	splash.set_payload({"name":"A fixture"})
	press(splash,JOY_BUTTON_Y)
	press(splash,JOY_BUTTON_A)
	test.check(splash.done,"A continues a held splash")
	splash.queue_free()
	await test.process_frame
