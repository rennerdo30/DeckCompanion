extends Node
signal completed(command: String, data: Dictionary)
var mock := false
var scenario := ""
var busy := false
var games: Array = []
var default_tool := false
var default_pending = null
var worker: Thread
var active := ""

func _ready() -> void:
	mock = "--mock" in OS.get_cmdline_user_args() or OS.get_environment("DECKCOMPANION_UI_MOCK") == "1"
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--scenario="): scenario = arg.trim_prefix("--scenario=")
	games = fixture("games").get("games", [])
	set_process(false)

static func fixture(name: String) -> Dictionary:
	var data: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://fixtures/%s.json" % name))
	for game in data.get("games", []):
		if game.get("art") is Dictionary:
			for kind in game.art:
				if game.art[kind] is String:
					var absolute := ProjectSettings.globalize_path(game.art[kind])
					if FileAccess.file_exists(absolute): game.art[kind] = absolute
	return data

static func failure(code: String, message: String) -> Dictionary:
	return {"error": {"code": code, "message": message}}

static func parse_response(raw: String, exit_code: int, command: String) -> Dictionary:
	var parser := JSON.new()
	if parser.parse(raw) != OK or not parser.data is Dictionary:
		return failure("INVALID_JSON", "The engine returned an invalid response. No result could be confirmed.")
	var data: Dictionary = parser.data
	if data.has("error"):
		if data.error is Dictionary and data.error.get("code") is String and data.error.get("message") is String: return data
		return failure("INVALID_RESPONSE", "The engine returned an invalid error response.")
	if exit_code != 0: return failure("COMMAND_FAILED", "The engine exited with code %s. Refresh before trying again." % exit_code)
	var valid := true
	match command:
		"games":
			valid = data.get("games") is Array
			if valid:
				for game in data.games:
					if not valid_game(game): valid = false
		"game", "enable", "disable", "mod":
			valid = valid_game(data)
			if command == "game": valid = valid and data.get("notes") is String and data.get("log_tail") is Array
			if data.get("log_tail") is Array:
				for line in data.log_tail:
					if not line is String: valid = false
		"status", "default":
			valid = data.get("version") is String and data.get("steam_running") is bool and data.get("pending") is Array and data.get("proton") is Array
			if valid:
				for build in data.proton:
					if not build is Dictionary or not build.get("name") is String or not build.get("path") is String: valid = false
		"update": valid = data.get("engine") is Dictionary and data.get("index") is Dictionary and data.engine.get("current") is String and data.engine.get("available") is String and (data.index.get("fetched_at") is String or data.index.get("fetched_at") == null)
		"apply": valid = data.get("applied") is Array and data.get("steam_restarted") is bool
	return data if valid else failure("INVALID_RESPONSE", "The engine response is missing required fields. Please update the engine and UI together.")

static func valid_game(value: Variant) -> bool:
	if not (value is Dictionary and value.get("appid") is String and value.get("name") is String and value.get("mods") is Array and value.get("enabled") is bool and value.get("has_recipe") is bool): return false
	if value.get("pending") != null and value.pending not in ["enable","disable"]: return false
	var launch: Variant = value.get("last_launch")
	if launch != null and (not launch is Dictionary or not launch.get("ok") is bool or not launch.get("time") is String): return false
	for mod in value.mods:
		if not mod is Dictionary or not mod.get("id") is String or not mod.get("name") is String or not mod.get("version") is String or not mod.get("enabled") is bool: return false
	return true

static func cli_path() -> String:
	var configured := OS.get_environment("DECKCOMPANION_CLI")
	if not configured.is_empty(): return configured
	var base := OS.get_environment("HOME").path_join(".local/share/deckcompanion")
	var stable := base.path_join("bin/deckcompanion")
	return stable if FileAccess.file_exists(stable) else base.path_join("current/bin/deckcompanion")

func request(command: String, args: Array = []) -> bool:
	if busy: return false
	busy = true
	active = command
	if mock:
		_mock_later(command, args)
	else:
		worker = Thread.new()
		var result := worker.start(_execute.bind(command, args.duplicate()))
		if result != OK:
			worker = null
			_finish(failure("START_FAILED", "Could not start the engine worker."))
		else: set_process(true)
	return true

func _execute(command: String, args: Array) -> Dictionary:
	var binary := cli_path()
	if not FileAccess.file_exists(binary): return failure("ENGINE_MISSING", "Engine not found. Install DeckCompanion or set DECKCOMPANION_CLI to its executable. Use --mock for a preview.")
	var arguments := [command] + args + ["--json"]
	var output: Array = []
	var code: int
	if OS.get_name() == "Windows":
		var payload := Marshalls.utf8_to_base64(JSON.stringify({"binary": binary, "args": arguments}))
		code = OS.execute("powershell.exe", ["-NoProfile", "-NonInteractive", "-File", ProjectSettings.globalize_path("res://scripts/execute.ps1"), "-Payload", payload], output)
	else:
		code = OS.execute("/usr/bin/timeout", ["--signal=TERM", "--kill-after=3s", "45s", binary] + arguments, output)
	if code == 124 or code == 137: return failure("TIMEOUT", "The engine timed out. Refresh to confirm its state before retrying.")
	return parse_response("".join(output), code, command)

func _process(_delta: float) -> void:
	if worker and not worker.is_alive():
		var data: Dictionary = worker.wait_to_finish()
		worker = null
		set_process(false)
		_finish(data)

func _exit_tree() -> void:
	if worker: worker.wait_to_finish()

func _finish(data: Dictionary) -> void:
	busy = false
	completed.emit(active, data)

func _mock_later(command: String, args: Array) -> void:
	await get_tree().create_timer(0.18).timeout
	_finish(mock_response(command, args))

func mock_response(command: String, args: Array = []) -> Dictionary:
	if scenario == "error" and command == "apply": return fixture("error")
	if scenario == "offline" and command == "update": return fixture("offline")
	if command == "games": return {"games": [] if scenario == "empty" else games.duplicate(true)}
	if command == "update": return fixture("update")
	if command == "default":
		default_pending = null if (args[0] == "--on") == default_tool else ("default_on" if args[0] == "--on" else "default_off")
		return mock_response("status")
	if command == "status":
		var result := fixture("status")
		result.pending = games.filter(func(g): return g.get("pending") != null).map(func(g): return {"appid": g.appid})
		if default_pending != null: result.pending.append({"action": default_pending})
		result.default_tool = default_tool
		return result
	if command == "apply":
		var applied: Array = []
		for game in games:
			if game.pending != null:
				applied.append(game.appid)
				if game.pending != null: game.enabled = game.pending == "enable"
				game.pending = null
		if default_pending != null:
			default_tool = default_pending == "default_on"
			applied.append(default_pending)
			default_pending = null
		return {"applied": applied, "steam_restarted": true}
	for game in games:
		if args.is_empty() or game.appid != args[0]: continue
		if command in ["enable", "disable"]:
			if not game.has_recipe: return failure("NO_RECIPE", "No recipe is available for this game.")
			game.pending = null if game.enabled == (command == "enable") else command
		if command == "mod":
			for mod in game.mods:
				if mod.id == args[1]:
					mod.enabled = args[2] == "--on"
					mod.state = "will_install_on_launch" if mod.enabled else "off"
					mod.state_message = "Will install on next launch" if mod.enabled else "Mod turned off. Takes effect on next launch."
					var response: Dictionary = game.duplicate(true)
					response.mod_result = {"changed_now": false, "message": mod.state_message}
					return response
		return game.duplicate(true)
	return failure("NOT_FOUND", "This game is no longer installed. Refresh your library.")
