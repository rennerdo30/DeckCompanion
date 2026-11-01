extends Node
# Decode local images on one worker. Only the latest waiting selection is retained.
signal loaded(key: String, texture: Texture2D)
const LIMIT := 8
var textures: Dictionary = {}
var recent: Array[String] = []
var worker: Thread
var active := ""
var waiting: Array[String] = []

static func paths(game: Dictionary) -> Array[String]:
	var result: Array[String] = []
	var art: Variant = game.get("art")
	if art is Dictionary:
		for kind in ["hero", "header", "portrait"]:
			var path: Variant = art.get(kind)
			if path is String and not path.is_empty(): result.append(path)
	return result

func request(paths_to_load: Array[String]) -> String:
	var key := JSON.stringify(paths_to_load)
	waiting = paths_to_load.duplicate()
	if textures.has(key):
		recent.erase(key)
		recent.append(key)
	elif not worker and not paths_to_load.is_empty(): _start(paths_to_load)
	return key

func _start(candidates: Array[String]) -> void:
	active = JSON.stringify(candidates)
	worker = Thread.new()
	if worker.start(_decode.bind(candidates.duplicate())) != OK: worker = null

static func _decode(candidates: Array[String]) -> Image:
	for path in candidates:
		if not FileAccess.file_exists(path): continue
		var image := Image.load_from_file(path)
		if image == null or image.is_empty(): continue
		var scale := minf(1.0, minf(1280.0 / image.get_width(), 800.0 / image.get_height()))
		if scale < 1.0: image.resize(maxi(1, int(image.get_width()*scale)), maxi(1, int(image.get_height()*scale)))
		return image
	return null

func _process(_delta: float) -> void:
	if worker and not worker.is_alive():
		var decoded: Image = worker.wait_to_finish()
		worker = null
		var texture: Texture2D = ImageTexture.create_from_image(decoded) if decoded != null else null
		textures[active] = texture
		recent.append(active)
		if recent.size() > LIMIT: textures.erase(recent.pop_front())
		loaded.emit(active, texture)
		if not waiting.is_empty() and not textures.has(JSON.stringify(waiting)): _start(waiting)

func _exit_tree() -> void:
	if worker: worker.wait_to_finish()
