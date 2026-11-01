extends Node
signal completed(command: String, data: Dictionary)
var mock := false
var scenario := ""
var busy := false
var games: Array = []
var default_tool := false
var takeover_snapshot: Variant = {"taken_at":"2026-11-01T13:31:00+00:00","games":6}
var restore_pending := false
var launcher_ticks := 0
var mock_plugin := false
var mock_launchers: Array = [
	{"id":"epic","name":"Epic Games Launcher","store":"egs","state":"not_installed","installed":false,"unattended":true,"warnings":[],"job":null},
	{"id":"gog","name":"GOG Galaxy","store":"gog","state":"installed","installed":true,"unattended":false,"warnings":[],"job":null},
	{"id":"ea","name":"EA app","store":"ea","state":"not_installed","installed":false,"unattended":false,"warnings":[],"job":null},
	{"id":"ubisoft","name":"Ubisoft Connect","store":"ubisoft","state":"not_installed","installed":false,"unattended":true,"warnings":[],"job":null},
	{"id":"battlenet","name":"Battle.net","store":"battlenet","state":"failed","installed":false,"unattended":false,"warnings":[],"job":{"active":false,"phase":"failed","error":"Installer exited with code 1"}}]
var default_pending = null
var removed_pending: Array = []
var worker: Thread
var active := ""
var settings := {"splash":true,"splash_seconds":4.0,"show_games_without_recipe":true,"check_updates_on_start":true,"auto_install_ge":true,"cleanup_old_ge":true,"splash_parallel":false,"takeover_all":true}
var proton_builds: Array = [{"name":"GE-Proton11-11","state":"wanted","installed":false,"wanted_by":["Farming Simulator 25"],"latest":true},{"name":"GE-Proton11-7","state":"installed","installed":true,"installed_bytes":1717986918,"removable":true}]
var proton_ticks := 0

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
		"proton":
			# `proton available` lists releases (source/page fields, no job state).
			valid = (data.get("active") is bool and (data.get("scheduled",false) or data.get("builds") is Array)) or (data.has("source") and data.get("builds") is Array)
			if valid and data.has("builds"):
				for build in data.builds:
					if not build is Dictionary or not build.get("name") is String or (not data.has("source") and not build.get("state") is String): valid = false
		"settings": valid = data.get("splash") is bool and (data.get("splash_seconds") is float or data.get("splash_seconds") is int) and data.splash_seconds >= 1 and data.splash_seconds <= 10 and data.get("show_games_without_recipe") is bool and data.get("check_updates_on_start") is bool
		"games":
			valid = data.get("games") is Array
			if valid:
				for game in data.games:
					if not valid_game(game): valid = false
		"game", "enable", "disable", "mod", "game-option":
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
		"update": valid = data.get("scheduled",false) or (data.get("engine") is Dictionary and data.get("index") is Dictionary and data.engine.get("current") is String and data.engine.get("available") is String and (data.index.get("fetched_at") is String or data.index.get("fetched_at") == null))
		"apply": valid = data.get("applied") is Array and data.get("steam_restarted") is bool
		"launchers": valid = data.get("launchers") is Array
		"decky": valid = data.has("installed") or data.has("needs_desktop") or data.has("can_install") or data.has("changed")
		"launcher": valid = data.get("launcher") is Dictionary or data.has("removed")
		"restore-all": valid = (data.get("restoring") is int or data.get("restoring") is float) and data.get("pending") is Array
		"add-game": valid = valid_game(data) and data.get("kind") == "non_steam" and data.get("slug") is String
		"remove-game": valid = data.get("removed") == true and data.get("prefix_preserved") == true
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
	if command == "proton":
		if not args.is_empty() and args[0] == "available":
			var releases: Array = []
			for i in 6:
				var name := "GE-Proton11-%d" % (11 - i)
				releases.append({"name": name, "published_at": "2026-10-%02dT12:00:00Z" % (19 - i*3), "size": 470000000, "digest_available": true, "installed": proton_builds.any(func(b): return b.name == name and b.get("installed",false))})
			return {"builds": releases, "source": "github", "page": 1, "per_page": 20, "has_more": true, "fetched_at": "2026-11-01T13:00:00Z", "cached": false, "stale": false, "warning": null}
		if not args.is_empty() and args[0] == "remove":
			proton_builds = proton_builds.filter(func(b): return b.name != args[1])
			return {"builds": proton_builds.duplicate(true), "active": false, "job": {}, "removed": args[1], "warnings": []}
		if not args.is_empty() and args[0] == "install":
			proton_ticks = 1
			return {"scheduled":true,"active":true}
		if proton_ticks > 0:
			proton_ticks += 1
			var build: Dictionary = proton_builds[0]
			build.state = "failed" if scenario == "download_failed" else ("installed" if proton_ticks >= 4 else "downloading")
			build.percent = 42 if proton_ticks == 2 else 85
			build.total_bytes = 564789552
			build.downloaded_bytes = int(build.total_bytes * build.percent / 100)
			build.installed = build.state == "installed"
			if build.state == "failed": build.error = "Not enough free disk space"
			if build.state in ["installed","failed"]: proton_ticks = 0
		return {"builds":proton_builds.duplicate(true),"active":proton_ticks > 0,"job":{}}
	if command == "add-game":
		var values := {}
		for i in range(0,args.size(),2): values[args[i]] = args[i+1]
		if str(values.get("--name","")).strip_edges().is_empty() or not str(values.get("--exe","")).to_lower().ends_with(".exe"): return failure("invalid_exe","Choose an .exe and enter a name")
		var game := {"appid":str(3000000000+games.size()),"slug":"mock-game-"+str(games.size()),"kind":"non_steam","name":values["--name"],"store":values.get("--store","none"),"exe":values["--exe"],"has_recipe":false,"installed":true,"enabled":true,"mods":[],"pending":null,"shortcut_pending":"add","last_launch":null,"notes":"","log_tail":[]}
		games.append(game)
		return game.duplicate(true)
	if command == "remove-game":
		games = games.filter(func(g): return g.get("slug") != args[0])
		removed_pending.append({"action":"remove","slug":args[0]})
		return {"slug":args[0],"removed":true,"prefix_preserved":true}
	if command == "settings":
		for i in args.size():
			if args[i] == "--splash": settings.splash = args[i+1] == "on"
			if args[i] == "--splash-seconds": settings.splash_seconds = clampf(float(args[i+1]),1,10)
			if args[i] == "--set":
				var pair: PackedStringArray = str(args[i+1]).split("=",true,1)
				if pair.size() != 2 or not settings.has(pair[0]): return failure("invalid_arguments","Unknown setting")
				if pair[0] == "splash_seconds": settings[pair[0]] = clampf(float(pair[1]),1,10)
				elif pair[1] in ["true","false","on","off"]: settings[pair[0]] = pair[1] in ["true","on"]
				else: return failure("invalid_arguments","Expected a boolean")
		return settings.duplicate()
	if scenario == "error" and command == "apply": return fixture("error")
	if scenario == "offline" and command == "update": return fixture("offline")
	if command == "games": return {"games": [] if scenario == "empty" else games.duplicate(true)}
	if command == "update": return fixture("update")
	if command == "decky":
		match args[0]:
			"status": return {"installed": true, "version": "v3.2.8", "service": "active", "latest": {"version": "v3.3.1", "source": "github", "stale": false}}
			"install-desktop": return {"switching": true, "autostart": "/home/deck/.config/autostart/deckcompanion-decky-install.desktop"}
			"install": return {"needs_desktop": true, "launched": false, "command": "curl -L https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/install_release.sh | sh", "instructions": ["Switch to Desktop Mode.", "Open Konsole and run the command.", "Enter your sudo password when the official installer asks."]}
			"plugin-status": return {"installed": mock_plugin, "version": "0.1.0" if mock_plugin else null, "decky_installed": true, "plugins_writable": true, "can_install": true, "instructions": "Use Decky Settings > Developer > Install Plugin from ZIP if this directory is not writable.", "latest": {"version": "0.1.0", "source": "github", "stale": false}}
			"plugin-install":
				mock_plugin = true
				return {"changed": true, "version": "0.1.0"}
		return failure("invalid_arguments", "Unknown decky action")
	if command == "launchers":
		for launcher in mock_launchers:
			if launcher.state == "installing":
				launcher_ticks += 1
				launcher.job.phase = ["downloading","installing","registering"][mini(launcher_ticks-1,2)]
				if launcher_ticks >= 4:
					launcher.state = "installed"
					launcher.installed = true
					launcher.job = null
					launcher_ticks = 0
		return {"launchers": mock_launchers.duplicate(true)}
	if command == "launcher":
		for launcher in mock_launchers:
			if launcher.id != args[1]: continue
			if args[0] == "install":
				launcher.state = "installing"
				launcher.job = {"active": true, "phase": "scheduled", "error": null}
				return {"scheduled": true, "launcher": launcher.duplicate(true)}
			launcher.state = "not_installed"
			launcher.installed = false
			return {"removed": true, "launcher": launcher.duplicate(true)}
		return failure("NOT_FOUND", "Unknown launcher")
	if command == "restore-all":
		var count: int = int(takeover_snapshot.get("games",0)) if takeover_snapshot is Dictionary else 0
		settings.takeover_all = false
		takeover_snapshot = null
		restore_pending = count > 0
		return {"restoring": count, "pending": [{"action":"restore"}] if count > 0 else []}
	if command == "game-option":
		for game in games:
			if args.is_empty() or game.appid != args[0]: continue
			for i in range(1,args.size()-1):
				if args[i] == "--takeover": game.takeover = "on" if args[i+1] == "on" else "excluded"
				if args[i] == "--proton":
					game.proton = {"name": game.get("proton",{}).get("name","GE-Proton11-11") if args[i+1] == "auto" else args[i+1], "reason": "newest" if args[i+1] == "auto" else "user_override"}
			return game.duplicate(true)
		return failure("NOT_FOUND", "This game is no longer installed. Refresh your library.")
	if command == "default":
		default_pending = null if (args[0] == "--on") == default_tool else ("default_on" if args[0] == "--on" else "default_off")
		return mock_response("status")
	if command == "status":
		var result := fixture("status")
		result.pending = games.filter(func(g): return g.get("pending") != null).map(func(g): return {"appid": g.appid})
		result.pending += games.filter(func(g): return g.get("shortcut_pending") != null).map(func(g): return {"slug":g.slug}) + removed_pending
		if default_pending != null: result.pending.append({"action": default_pending})
		result.default_tool = default_tool
		result.takeover_snapshot = takeover_snapshot
		if restore_pending: result.pending.append({"action":"restore"})
		result.install_path = "/home/deck/.local/share/deckcompanion"
		return result
	if command == "apply":
		var applied: Array = []
		restore_pending = false
		applied.append_array(removed_pending)
		removed_pending.clear()
		for game in games:
			if game.get("shortcut_pending") != null:
				applied.append(game.appid)
				game.shortcut_pending = null
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
