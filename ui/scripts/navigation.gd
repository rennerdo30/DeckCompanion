extends RefCounted

static func visible_games(games: Array, filter: int) -> Array:
	return games.filter(func(g): return g.get("installed", false) and (filter == 0 or (filter == 1 and g.get("has_recipe", false)) or (filter == 2 and g.get("enabled", false))))

static func next_index(index: int, direction: int, count: int) -> int:
	return posmod(index + direction, count) if count > 0 else -1

static func wire(controls: Array) -> void:
	# D-pad directions use Godot's geometric focus search (the nearest control in that
	# direction). Forcing up/down to the list order sent "down" to the tab button on the
	# right on the Deck (owner report, 2026-11-01). Only Tab/Shift+Tab follow list order.
	for i in controls.size():
		var control: Control = controls[i]
		control.focus_neighbor_top = NodePath()
		control.focus_neighbor_bottom = NodePath()
		control.focus_next = control.get_path_to(controls[next_index(i, 1, controls.size())])
		control.focus_previous = control.get_path_to(controls[next_index(i, -1, controls.size())])

static func sort_games(games: Array) -> Array:
	# Most recently played first (Steam's LastPlayed), never-played games after, then by name.
	var sorted := games.duplicate()
	sorted.sort_custom(func(a, b):
		var la := int(a.get("last_played", 0) if a.get("last_played") != null else 0)
		var lb := int(b.get("last_played", 0) if b.get("last_played") != null else 0)
		if la != lb: return la > lb
		return str(a.get("name","")).naturalnocasecmp_to(str(b.get("name",""))) < 0)
	return sorted
