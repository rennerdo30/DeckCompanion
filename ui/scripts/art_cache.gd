extends Node
# Decodes local game art on one worker thread.
# Every request is queued (library thumbnails must all load, owner report DC-40); the
# focused game jumps the queue. Decoded images are downscaled once and kept on disk in
# user://art-cache/ so the next session shows artwork immediately.
signal loaded(key: String, texture: Texture2D)
const LIMIT := 96
const MAX_SIZE := Vector2(960.0, 540.0)
const DISK := "user://art-cache"
var textures: Dictionary = {}
var recent: Array[String] = []
var worker: Thread
var active := ""
var queue: Array = []  # [key, paths]

static func paths(game: Dictionary) -> Array[String]:
	var result: Array[String] = []
	var art: Variant = game.get("art")
	if art is Dictionary:
		for kind in ["hero", "header", "portrait"]:
			var path: Variant = art.get(kind)
			if path is String and not path.is_empty(): result.append(path)
	return result

## Queue `paths_to_load`; `urgent` (the focused game) is decoded next.
func request(paths_to_load: Array[String], urgent := true) -> String:
	var key := JSON.stringify(paths_to_load)
	if paths_to_load.is_empty(): return key
	if textures.has(key):
		recent.erase(key)
		recent.append(key)
		return key
	if key == active: return key
	for i in queue.size():
		if queue[i][0] == key:
			if urgent and i > 0:
				queue.push_front(queue.pop_at(i))
			return key
	if urgent: queue.push_front([key, paths_to_load.duplicate()])
	else: queue.append([key, paths_to_load.duplicate()])
	_next()
	return key

func _next() -> void:
	if worker or queue.is_empty(): return
	var job: Array = queue.pop_front()
	active = job[0]
	worker = Thread.new()
	if worker.start(_decode.bind(job[1].duplicate(), DISK)) != OK:
		worker = null
		active = ""

static func _cache_file(disk: String, path: String) -> String:
	var stamp := FileAccess.get_modified_time(path)
	return disk.path_join((path + "@" + str(stamp)).md5_text() + ".png")

static func _decode(candidates: Array[String], disk: String) -> Image:
	for path in candidates:
		if not FileAccess.file_exists(path): continue
		var cached := _cache_file(disk, path)
		if FileAccess.file_exists(cached):
			var hit := Image.load_from_file(ProjectSettings.globalize_path(cached))
			if hit != null and not hit.is_empty(): return hit
		var image := Image.load_from_file(path)
		if image == null or image.is_empty(): continue
		var scale := minf(1.0, minf(MAX_SIZE.x / image.get_width(), MAX_SIZE.y / image.get_height()))
		if scale < 1.0: image.resize(maxi(1, int(image.get_width()*scale)), maxi(1, int(image.get_height()*scale)), Image.INTERPOLATE_LANCZOS)
		DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(disk))
		image.save_png(ProjectSettings.globalize_path(cached))
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
		var done := active
		active = ""
		loaded.emit(done, texture)
		_next()

func _exit_tree() -> void:
	if worker: worker.wait_to_finish()
