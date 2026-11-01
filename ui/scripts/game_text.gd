extends RefCounted

static func fix(game: Dictionary) -> String:
	if game.get("kind") == "non_steam" and not game.get("has_recipe",false): return "umu launcher · Own save prefix"
	if not game.get("has_recipe", false): return "No recipe — Steam launches it normally"
	if game.get("pending") != null: return "Pending " + str(game.pending)
	return "Enabled" if game.get("enabled", false) else "Fix available"

static func proton(game: Dictionary) -> String:
	var build: Variant = game.get("proton")
	if not build is Dictionary: return "Proton · Not reported by engine"
	var reasons := {"pinned": "recipe pin", "recipe_fallback": "recipe fallback", "newest": "newest installed", "valve_recommended": "Valve recommendation", "user_override": "your choice", "community_fix": "has a community fix"}
	return str(build.get("name", "Unknown Proton")) + " · " + str(reasons.get(build.get("reason"), "not reported"))

## One short line about umu-protonfixes for this game, or "" when there is none.
static func community(game: Dictionary) -> String:
	match game.get("community_fix"):
		"applied": return "Community fix runs with " + str(game.get("community_fix_source", "GE-Proton"))
		"available_not_installed": return "Community fix available with GE-Proton (not installed)"
	return ""

static func version(value: Variant) -> String:
	if value == null or str(value).is_empty() or value == "unresolved": return "latest"
	return str(value) if str(value).begins_with("v") else "v" + str(value)

static func mod_label(mod: Dictionary) -> String:
	var state: Variant = mod.get("state")
	var installed: Variant = mod.get("installed_version")
	var install := ""
	match state:
		"installed": install = "Installed" + (" " + version(installed) if installed != null else "")
		"will_install_on_launch": install = "Will install on next launch"
		"update_available": install = "Update available"
		"blocked": install = "Blocked: " + str(mod.get("state_message", "Installation unavailable"))
		"off": install = "Off"
		_:
			if not mod.get("enabled", false): install = "Off"
			elif installed == null: install = "Will install on next launch"
			else:
				var wanted: Variant = mod.get("version")
				var newer: bool = wanted is String and wanted not in ["", "unresolved"] and version(installed) != version(wanted)
				install = ("Update available · Installed " if newer else "Installed ") + version(installed)
	return "[%s] %s %s — %s" % ["On" if mod.get("enabled", false) else "Off", mod.get("name", "Mod"), version(mod.get("version")), install]

static func launch(game: Dictionary) -> String:
	var result: Variant = game.get("last_launch")
	if not result is Dictionary: return "No launch recorded"
	return ("Last handoff OK" if result.get("ok", false) else "Last launch preparation failed") + " · " + str(result.get("time", "")).replace("T", " ").replace("Z", " UTC")

static func note(game: Dictionary) -> String:
	var notes: Variant = game.get("notes")
	return notes.strip_edges().get_slice("\n", 0) if notes is String else ""

static func mod_message(data: Dictionary) -> String:
	var result: Variant = data.get("mod_result")
	if result is Dictionary and result.get("message") is String and not result.message.strip_edges().is_empty(): return result.message
	return "Saved. Takes effect on next launch."
