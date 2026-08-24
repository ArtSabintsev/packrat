from pathlib import Path

SHARE_DIR = Path.home() / ".local/share/packrat"
DEFAULT_CSV = SHARE_DIR / "codes.csv"
RESULTS_DIR = SHARE_DIR / "results"


def ensure_dirs() -> None:
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
