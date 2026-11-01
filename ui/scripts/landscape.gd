extends Control
# Original vector landscape, drawn with built-in primitives. No external assets.
func _draw() -> void:
	var s := size
	draw_rect(Rect2(Vector2.ZERO, s), Color("173b43"))
	draw_circle(Vector2(s.x * 0.76, s.y * 0.27), s.y * 0.15, Color("e9dca3"))
	draw_colored_polygon(PackedVector2Array([Vector2(0,s.y*0.6),Vector2(s.x*0.35,s.y*0.3),Vector2(s.x,s.y*0.65),s,Vector2(0,s.y)]), Color("367567"))
	draw_colored_polygon(PackedVector2Array([Vector2(0,s.y*0.72),Vector2(s.x*0.6,s.y*0.48),Vector2(s.x,s.y*0.6),s,Vector2(0,s.y)]), Color("7f995b"))
	for i in 9:
		var x := float(i) / 8 * s.x
		draw_line(Vector2(s.x*0.6,s.y*0.55),Vector2(x,s.y),Color("bdc482"),2,true)
	draw_rect(Rect2(s.x*0.17,s.y*0.56,s.x*0.16,s.y*0.18),Color("182f32"))
	draw_rect(Rect2(s.x*0.22,s.y*0.48,s.x*0.08,s.y*0.12),Color("bcd6c2"))
	for x in [0.2,0.31]:
		draw_circle(Vector2(s.x*x,s.y*0.75),s.y*0.065,Color("10272e"))
		draw_circle(Vector2(s.x*x,s.y*0.75),s.y*0.025,Color("a5bd9d"))
