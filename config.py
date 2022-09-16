import os

LOG_FILE = "/tmp/DeckCompanion.log"
LOG_FORMAT = "%(asctime)s %(message)s"
LOG_DATE_FORMAT = "%m/%d/%Y %I:%M:%S %p"

USER = os.getenv("USER")
BASE_DIR = os.getenv("STEAM_COMPAT_TOOL_PATHS")
GAME_ID = os.getenv("SteamGameId")
GAME_CONFIG_DIR = BASE_DIR + "/games/" + str(GAME_ID)
GAME_CONFIG_PATH_DEFAULT = BASE_DIR + "/games/config.json"

tmp_path = GAME_CONFIG_DIR + "/config.json"
GAME_CONFIG_PATH = tmp_path if os.path.exists(tmp_path) else GAME_CONFIG_PATH_DEFAULT
