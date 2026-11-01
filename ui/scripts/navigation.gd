extends RefCounted

static func visible_games(games: Array, filter: int) -> Array:
	return games.filter(func(g): return g.get("installed", false) and (filter == 0 or (filter == 1 and g.get("has_recipe", false)) or (filter == 2 and g.get("enabled", false))))

static func next_index(index: int, direction: int, count: int) -> int:
	return posmod(index + direction, count) if count > 0 else -1

static func wire(controls: Array) -> void:
	for i in controls.size():
		var control: Control = controls[i]
		control.focus_neighbor_top = control.get_path_to(controls[next_index(i, -1, controls.size())])
		control.focus_neighbor_bottom = control.get_path_to(controls[next_index(i, 1, controls.size())])
		control.focus_next = control.focus_neighbor_bottom
		control.focus_previous = control.focus_neighbor_top
