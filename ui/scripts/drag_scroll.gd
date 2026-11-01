extends ScrollContainer
# Watch input before child buttons consume it; leave presses/taps to Godot.
const THRESHOLD := 12.0
static var gesture_owner: WeakRef
var pointer := ""
var finger := -1
var origin := Vector2.ZERO
var previous := Vector2.ZERO
var dragging := false
var velocity := Vector2.ZERO
var position_float := Vector2.ZERO
var last_motion := 0
var cancelled_buttons: Array[BaseButton] = []

func _ready() -> void:
	# This helper owns drag physics even when X11 reports no touchscreen.
	scroll_deadzone = 1000000

func scrollable() -> bool:
	return get_v_scroll_bar().max_value > get_v_scroll_bar().page or get_h_scroll_bar().max_value > get_h_scroll_bar().page

func owns(point: Vector2) -> bool:
	if not is_visible_in_tree() or not get_global_rect().has_point(point) or not scrollable(): return false
	for bar in [get_v_scroll_bar(),get_h_scroll_bar()]:
		if bar.is_visible_in_tree() and bar.get_global_rect().has_point(point): return false
	for nested in find_children("*","ScrollContainer",true,false):
		if nested.is_visible_in_tree() and nested.get_global_rect().has_point(point) and (nested.get_v_scroll_bar().max_value > nested.get_v_scroll_bar().page or nested.get_h_scroll_bar().max_value > nested.get_h_scroll_bar().page): return false
	return true

func _input(event: InputEvent) -> void:
	if event is InputEventJoypadButton or event is InputEventJoypadMotion or event is InputEventKey:
		velocity = Vector2.ZERO
		return
	var kind := ""
	var pressed := false
	var released := false
	var motion := false
	var point := Vector2.ZERO
	if event is InputEventScreenTouch:
		kind = "touch"
		pressed = event.pressed
		released = not event.pressed
		point = event.position
		if not pointer.is_empty() and event.index != finger: return
	elif event is InputEventScreenDrag:
		kind = "touch"
		motion = true
		point = event.position
		if event.index != finger: return
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		kind = "mouse"
		pressed = event.pressed
		released = not event.pressed
		point = event.position
	elif event is InputEventMouseMotion:
		kind = "mouse"
		motion = (event.button_mask & MOUSE_BUTTON_MASK_LEFT) != 0
		point = event.position
	else: return
	if pressed and pointer.is_empty() and owns(point):
		if gesture_owner != null and gesture_owner.get_ref() != null and gesture_owner.get_ref() != self: return
		gesture_owner = weakref(self)
		pointer = kind
		finger = event.index if event is InputEventScreenTouch else -1
		origin = point
		previous = point
		velocity = Vector2.ZERO
		position_float = Vector2(scroll_horizontal,scroll_vertical)
		last_motion = Time.get_ticks_msec()
		return
	if pointer != kind or pointer.is_empty(): return
	if motion:
		if not dragging and point.distance_to(origin) >= THRESHOLD:
			dragging = true
			scroll_started.emit()
			for button in find_children("*","BaseButton",true,false):
				if not button.disabled and button.get_global_rect().has_point(origin):
					cancelled_buttons.append(button)
					button.disabled = true # Clears Godot's pending click, including emulated mouse.
		if dragging:
			var now := Time.get_ticks_msec()
			var movement := previous - point
			velocity = (movement / maxf((now-last_motion)/1000.0,0.016)).limit_length(1400)
			_move(origin-point if previous == origin else movement)
			get_viewport().set_input_as_handled()
		last_motion = Time.get_ticks_msec()
		previous = point
	elif released:
		if dragging:
			get_viewport().set_input_as_handled()
			if Time.get_ticks_msec()-last_motion > 100: velocity = Vector2.ZERO
		pointer = ""
		dragging = false
		gesture_owner = null
		_restore_buttons.call_deferred()

func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_WINDOW_FOCUS_OUT:
		pointer = ""
		dragging = false
		velocity = Vector2.ZERO
		if gesture_owner != null and gesture_owner.get_ref() == self: gesture_owner = null
		_restore_buttons()

func _restore_buttons() -> void:
	for button in cancelled_buttons:
		if is_instance_valid(button): button.disabled = false
	cancelled_buttons.clear()

func _move(amount: Vector2) -> void:
	position_float.x = clampf(position_float.x+amount.x,0,maxf(0,get_h_scroll_bar().max_value-get_h_scroll_bar().page))
	position_float.y = clampf(position_float.y+amount.y,0,maxf(0,get_v_scroll_bar().max_value-get_v_scroll_bar().page))
	scroll_horizontal = roundi(position_float.x)
	scroll_vertical = roundi(position_float.y)

func _process(delta: float) -> void:
	if not pointer.is_empty() or velocity.length() < 8: return
	_move(velocity*delta)
	velocity = velocity.move_toward(Vector2.ZERO,2200*delta)
