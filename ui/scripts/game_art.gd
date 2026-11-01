extends Control
# Original geometric fallback, keyed to the game. No network or third-party assets.
const Cache = preload("res://scripts/art_cache.gd")
var cache: Node
var key := ""
var game_id := ""
var texture: TextureRect
var fallback := true
## Thumbnails queue behind the focused game; previews and banners load first.
var urgent := true

func _ready() -> void:
	clip_contents = true
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	texture = TextureRect.new()
	texture.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	texture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	texture.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
	texture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(texture)
	cache.loaded.connect(_loaded)
	resized.connect(queue_redraw)

func show_game(game: Dictionary) -> void:
	game_id = str(game.get("appid", ""))
	key = cache.request(Cache.paths(game), urgent)
	texture.texture = cache.textures.get(key)
	fallback = texture.texture == null
	queue_redraw()

func _loaded(loaded_key: String, image: Texture2D) -> void:
	if loaded_key != key: return
	texture.texture = image
	fallback = image == null
	queue_redraw()

func _draw() -> void:
	var s := size
	if s.x <= 0 or s.y <= 0: return
	draw_rect(Rect2(Vector2.ZERO, s), Color("183c44"))
	var offset := float(absi(game_id.hash()) % 100) / 100.0
	draw_circle(Vector2(s.x * (0.65 + offset*0.15), s.y*0.35), s.y*0.23, Color("b8d49b"))
	for i in 4:
		var y := s.y * (0.42 + i*0.12)
		draw_colored_polygon(PackedVector2Array([Vector2(0,y+s.y*0.1), Vector2(s.x*0.38,y-s.y*(0.2+offset*0.1)), Vector2(s.x,y+s.y*0.05), s, Vector2(0,s.y)]), Color("244d51").lerp(Color("60836b"), i/4.0))
