import os

USER = os.getenv("USER")
BASE_DIR = os.getenv("STEAM_COMPAT_TOOL_PATHS")
GAME_ID = os.getenv("SteamGameId")
GAME_CONFIG_DIR = BASE_DIR + "/games"
GAME_CONFIG_PATH_DEFAULT = GAME_CONFIG_DIR + "/config.json"

tmp_path = GAME_CONFIG_DIR + "/" + str(GAME_ID) + "/config.json"
GAME_CONFIG_PATH = tmp_path if os.path.exists(tmp_path) else GAME_CONFIG_PATH_DEFAULT
