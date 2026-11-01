extends Control
var keyboard := false
var detail := false
var library_preview := false

func _draw() -> void:
	var font := ThemeDB.fallback_font
	var keys := ["Enter", "Esc", "X", "Y", "Q / E", "PgUp/Dn"] if keyboard else ["A", "B", "X", "Y", "LB / RB", "R"]
	var labels := ["Select", "Back", "Toggle" if detail else "Filter", "Apply", "Tabs", "Scroll log" if detail else "Scroll preview"]
	if not detail and not library_preview:
		keys.resize(5)
		labels.resize(5)
	var x := 0.0
	for i in keys.size():
		var width := maxf(26, font.get_string_size(keys[i], HORIZONTAL_ALIGNMENT_LEFT, -1, 14).x + 14)
		var box := StyleBoxFlat.new()
		box.bg_color = Color("1d3240")
		box.border_color = Color("3c5a66")
		box.set_border_width_all(1)
		box.set_corner_radius_all(6 if keyboard or i > 3 else 13)
		draw_style_box(box, Rect2(x, 4, width, 26))
		draw_string(font, Vector2(x+7,22),keys[i],HORIZONTAL_ALIGNMENT_LEFT,-1,14,Color("d8f7ed"))
		draw_string(font, Vector2(x+width+7,23),labels[i],HORIZONTAL_ALIGNMENT_LEFT,-1,16,Color("8fa6b2"))
		x += width + font.get_string_size(labels[i],HORIZONTAL_ALIGNMENT_LEFT,-1,16).x + 24
