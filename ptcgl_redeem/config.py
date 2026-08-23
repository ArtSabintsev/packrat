from pathlib import Path

LOGIN_URL = "https://redeem.tcg.pokemon.com/en-us/"
CHUNK_SIZE = 10
DEFAULT_CDP_PORT = 9333
PACE_MIN = 2.0
PACE_MAX = 4.0

BRAVE_BIN = Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
CHROME_BIN = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
CHROMIUM_BIN = Path("/Applications/Chromium.app/Contents/MacOS/Chromium")

STATE_DIR = Path.home() / ".local/state/ptcgl-redeem"
SHARE_DIR = Path.home() / ".local/share/ptcgl-redeem"
PROFILE_DIR = STATE_DIR / "browser-profile"
DEFAULT_CSV = SHARE_DIR / "codes.csv"
RESULTS_DIR = SHARE_DIR / "results"


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
