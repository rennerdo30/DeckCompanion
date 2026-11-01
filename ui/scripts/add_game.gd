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
	host._chip("‹  Back",back,heading,"add-back")
	var title: Label = host._label("Add a game",26)
	title.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	heading.add_child(title)
	if not exe.is_empty():
		_name_form()
		return
	var columns: HBoxContainer = host._row(host.body)
	columns.size_flags_vertical = Control.SIZE_EXPAND_FILL
	columns.add_theme_constant_override("separation",14)
	# Left: launcher presets (installed through umu with their own prefix).
	var left := DragScroll.new()
	left.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	left.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	left.follow_focus = true
	columns.add_child(left)
	var launchers: VBoxContainer = host._card(_column(left),"Install a store launcher")
	host._paragraph(launchers,"Each launcher gets its own prefix and a Steam library entry. Sign in after it installs.")
	if host.launchers_data.is_empty(): host._paragraph(launchers,"Loading launchers…")
	for launcher in host.launchers_data: _launcher_row(launchers,launcher)
	# Right: any Windows .exe.
	var right := DragScroll.new()
	right.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	right.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	right.follow_focus = true
	columns.add_child(right)
	host.screen_scroll = right
	var browser: VBoxContainer = host._card(_column(right),"Add a Windows game (.exe)")
	var roots := HFlowContainer.new()
	roots.add_theme_constant_override("h_separation",6)
	roots.add_theme_constant_override("v_separation",6)
	browser.add_child(roots)
	var places := locations()
	for path in places: host._chip("Home" if path == places[0] else str(path).get_file(),browse.bind(path),roots,"location:"+path,folder == path)
	host._paragraph(browser,folder,host.MINT)
	host._chip("↑  Parent folder",browse.bind(folder.get_base_dir()),browser,"add-up")
	var items := entries()
	if not problem.is_empty(): host._paragraph(browser,problem)
	elif items.is_empty(): host._paragraph(browser,"No folders or .exe files here. Try another location.")
	for item in items:
		var button: Button = host._button(("▸  " if item.directory else "＋  ") + item.name,choose.bind(item),browser,"file:"+item.name)
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT

func _column(scroll: ScrollContainer) -> VBoxContainer:
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(column)
	return column

func _launcher_row(parent: Node, launcher: Dictionary) -> void:
	var state: String = str(launcher.get("state","not_installed"))
	var job: Variant = launcher.get("job")
	var line: String = {"not_installed": "Not installed" + ("" if launcher.get("unattended",false) else "  ·  the installer may ask you to click through"),
		"installing": "Installing  ·  " + (str(job.get("phase","")).capitalize() if job is Dictionary else "starting"),
		"installed": "Installed  ·  in your Steam library",
		"failed": "Failed: " + (str(job.get("error","")) if job is Dictionary else "unknown error")}.get(state,state)
	var row: HBoxContainer = host._setting_row(parent,str(launcher.get("name","Launcher")),line)
	var id: String = str(launcher.get("id",""))
	match state:
		"installed": host._chip("Remove",func(): host.launcher_backend.request("launcher",["remove",id]),row,"launcher-remove:"+id,false,host.WARN,not host.launcher_backend.busy)
		"installing": host._badge(row,"WORKING",host.CREAM).size_flags_vertical = Control.SIZE_SHRINK_CENTER
		_: host._chip("Retry" if state == "failed" else "Install",func(): host.launcher_backend.request("launcher",["install",id]),row,"launcher:"+id,false,host.MINT,not host.launcher_backend.busy and not host.launchers_busy())

func _name_form() -> void:
	var scroll := DragScroll.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.follow_focus = true
	host.body.add_child(scroll)
	host.screen_scroll = scroll
	var column := _column(scroll)
	var card: VBoxContainer = host._card(column,"Windows game  /  non-Steam")
	host._paragraph(card,exe)
	name_input = LineEdit.new()
	name_input.text = game_name
	name_input.max_length = 128
	name_input.custom_minimum_size.y = 48
	name_input.add_theme_font_size_override("font_size",20)
	name_input.placeholder_text = "Game name"
	name_input.set_meta("focus_id","add-name")
	name_input.text_changed.connect(set_name)
	card.add_child(name_input)
	host.controls.append(name_input)
	var row: HBoxContainer = host._row(card)
	row.add_theme_constant_override("separation",6)
	for option in ["none","egs","gog"]:
		host._chip({"none":"Standalone","egs":"Epic Games","gog":"GOG"}[option],func(): store = option; host._render(),row,"store:"+option,store == option)
	host._paragraph(card,"Adds a Steam library entry with its own save prefix. Apply once to update Steam. Your original files stay in place.")
	var actions: HBoxContainer = host._row(card)
	host._chip("Add to library",submit,actions,"add-submit",true,host.MINT,not game_name.strip_edges().is_empty())
	host._chip("Hide keyboard" if keys else "Edit name with controller",func(): keys = not keys; host._render(),actions,"add-keyboard")
	if keys:
		var grid := GridContainer.new()
		grid.columns = 10
		column.add_child(grid)
		for character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
			host._button(character,type_key.bind(character),grid,"key:"+character)
		host._button("Space",type_key.bind(" "),grid,"key:space")
		host._button("⌫",type_key.bind("⌫"),grid,"key:erase")
