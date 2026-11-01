extends Control
var keyboard := false
var detail := false

func _draw() -> void:
	var font := ThemeDB.fallback_font
	var keys := ["Enter", "Esc", "X", "Y", "Q / E", "PgUp/Dn"] if keyboard else ["A", "B", "X", "Y", "LB / RB", "R"]
	var labels := ["Select", "Back", "Toggle" if detail else "Filter", "Apply", "Tabs", "Scroll log"]
	if not detail:
		keys.resize(5)
		labels.resize(5)
	var x := 0.0
	for i in keys.size():
		var width := maxf(30, font.get_string_size(keys[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 15).x + 16)
		var box := StyleBoxFlat.new()
		box.bg_color = Color("223642")
		box.border_color = Color("57737d")
		box.set_border_width_all(1)
		box.set_corner_radius_all(7 if keyboard or i > 3 else 16)
		draw_style_box(box, Rect2(x, 7, width, 30))
		draw_string(font, Vector2(x+8,28),keys[i],HORIZONTAL_ALIGNMENT_LEFT,-1,15,Color("d8f7ed"))
		draw_string(font, Vector2(x+width+8,28),labels[i],HORIZONTAL_ALIGNMENT_LEFT,-1,16,Color("9eb3bf"))
		x += width + font.get_string_size(labels[i],HORIZONTAL_ALIGNMENT_LEFT,-1,16).x + 32
