extends RefCounted

static func visible_games(games: Array, filter: int) -> Array:
	# 0 All, 1 Has fix, 2 Enabled, 3 Non-Steam, 4 Needs attention.
	return games.filter(func(g):
		if not g.get("installed", false): return false
		match filter:
			1: return g.get("has_recipe", false)
			2: return g.get("enabled", false)
			3: return g.get("kind") == "non_steam"
			4: return needs_attention(g)
		return true)

static func needs_attention(game: Dictionary) -> bool:
	var launch: Variant = game.get("last_launch")
	if launch is Dictionary and not launch.get("ok", true): return true
	for mod in game.get("mods", []):
		if mod is Dictionary and mod.get("state") == "blocked": return true
	return false

static func next_index(index: int, direction: int, count: int) -> int:
	return posmod(index + direction, count) if count > 0 else -1

static func wire(controls: Array) -> void:
	# D-pad directions use Godot's geometric focus search (the nearest control in that
	# direction). Forcing up/down to the list order sent "down" to the tab button on the
	# right on the Deck (owner report, 2026-11-01). Only Tab/Shift+Tab follow list order.
	for i in controls.size():
		var control: Control = controls[i]
		control.focus_next = control.get_path_to(controls[next_index(i, 1, controls.size())])
		control.focus_previous = control.get_path_to(controls[next_index(i, -1, controls.size())])

static func sort_games(games: Array, by_name := false) -> Array:
	# Default: most recently played first (Steam's LastPlayed), never played after, then name.
	var sorted := games.duplicate()
	sorted.sort_custom(func(a, b):
		var la := int(a.get("last_played", 0) if a.get("last_played") != null else 0)
		var lb := int(b.get("last_played", 0) if b.get("last_played") != null else 0)
		if not by_name and la != lb: return la > lb
		return str(a.get("name","")).naturalnocasecmp_to(str(b.get("name",""))) < 0)
	return sorted
