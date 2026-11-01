extends RefCounted
const DragScroll = preload("res://scripts/drag_scroll.gd")
var host: Control
var folder := ""
var exe := ""
var game_name := ""
var store := "none"
var name_input: LineEdit
var keys := false
var problem := ""

func _init(owner_ui: Control) -> void:
	host = owner_ui
	folder = "/home/deck" if host.backend.mock else OS.get_environment("HOME")
	if folder.is_empty(): folder = OS.get_environment("USERPROFILE")
	if folder.is_empty(): folder = "/"

func locations() -> Array:
	if host.backend.mock: return ["/home/deck","/run/media/deck/GAMES"]
	var result: Array = [OS.get_environment("HOME") if OS.get_name() != "Windows" else OS.get_environment("USERPROFILE")]
	for root in ["/run/media", "/media", "/mnt"]:
		if DirAccess.dir_exists_absolute(root):
			result.append(root)
			for user in DirAccess.get_directories_at(root):
				var path: String = root.path_join(user)
				for mount in DirAccess.get_directories_at(path): result.append(path.path_join(mount))
	return result

func entries() -> Array:
	problem = ""
	if host.backend.mock:
		return [{"name":"Games", "directory":true}] if folder == "/home/deck" else [{"name":"Orbit Trails.exe", "directory":false}]
	var dir := DirAccess.open(folder)
	if dir == null:
		problem = "This folder is unavailable. Choose Home or another location."
		return []
	var dirs: Array = []
	var files: Array = []
	for item in dir.get_directories():
		dirs.append({"name":item, "directory":true})
	for item in dir.get_files():
		if item.get_extension().to_lower() == "exe": files.append({"name":item, "directory":false})
	return dirs + files

func choose(item: Dictionary) -> void:
	if item.directory:
		folder = folder.path_join(item.name)
	else:
		exe = folder.path_join(item.name)
		game_name = str(item.name).get_basename().replace("_"," ").replace("-"," ")
	host.last_focus = "add-back"
	host._render()

func browse(path: String) -> void:
	folder = path
	host.last_focus = "add-up"
	host._render()

func cancel() -> void:
	host.add_flow = null
	host.last_focus = "add-game"
	host._render()

func back() -> void:
	if not exe.is_empty():
		exe = ""
		keys = false
		host._render()
	else: cancel()

func submit() -> void:
	if game_name.strip_edges().is_empty(): return
	host._request("add-game", ["--exe",exe,"--name",game_name.strip_edges(),"--store",store])

func type_key(value: String) -> void:
	set_name((game_name.left(-1) if value == "⌫" else game_name + value).left(128))
	name_input.text = game_name

func set_name(value: String) -> void:
	game_name = value
	for button in host.controls:
		if button.get_meta("focus_id","") == "add-submit": button.disabled = game_name.strip_edges().is_empty() or host.backend.busy

func render() -> void:
	var heading: HBoxContainer = host._row(host.body)
	host._button("‹  Back",back,heading,"add-back")
	heading.add_child(host._label("Add a game",28))
	var scroll := DragScroll.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	host.body.add_child(scroll)
	host.screen_scroll = scroll
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(column)
	if exe.is_empty():
		host._paragraph(column,"Choose a Windows .exe  ·  Folders and mounted drives are available below.")
		var roots := GridContainer.new()
		roots.columns = 4
		column.add_child(roots)
		var places := locations()
		for path in places: host._button("Home" if path == places[0] else str(path).get_file(),browse.bind(path),roots,"location:"+path)
		host._paragraph(column,folder,host.MINT)
		host._button("↑  Parent folder",browse.bind(folder.get_base_dir()),column,"add-up")
		var items := entries()
		if not problem.is_empty(): host._paragraph(column,problem)
		elif items.is_empty(): host._paragraph(column,"No folders or .exe files here. Try another location.")
		for item in items:
			host._button(("▸  " if item.directory else "＋  ") + item.name,choose.bind(item),column,"file:"+item.name)
		return
	var card: VBoxContainer = host._card(column)
	host._paragraph(card,"WINDOWS GAME  /  NON-STEAM",host.MINT)
	host._paragraph(card,exe)
	name_input = LineEdit.new()
	name_input.text = game_name
	name_input.max_length = 128
	name_input.custom_minimum_size.y = 48
	name_input.placeholder_text = "Game name"
	name_input.set_meta("focus_id","add-name")
	name_input.text_changed.connect(set_name)
	card.add_child(name_input)
	host.controls.append(name_input)
	var row: HBoxContainer = host._row(card)
	for option in ["none","egs","gog"]:
		host._button(({"none":"Standalone","egs":"Epic Games","gog":"GOG"}[option]) + ("  ✓" if store == option else ""),func(): store = option; host._render(),row,"store:"+option)
	host._paragraph(card,"Adds a Steam library shortcut with its own save prefix. Apply once to update Steam. Your original files stay in place.")
	var actions: HBoxContainer = host._row(card)
	host._button("Add to library",submit,actions,"add-submit",not game_name.strip_edges().is_empty())
	host._button("Hide keyboard" if keys else "Edit name with controller",func(): keys = not keys; host._render(),actions,"add-keyboard")
	if keys:
		var grid := GridContainer.new()
		grid.columns = 10
		column.add_child(grid)
		for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
			host._button(character,type_key.bind(character),grid,"key:"+character)
		host._button("Space",type_key.bind(" "),grid,"key:space")
		host._button("⌫",type_key.bind("⌫"),grid,"key:erase")
