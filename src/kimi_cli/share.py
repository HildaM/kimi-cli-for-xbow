from pathlib import Path


def get_share_dir() -> Path:
    """Get the share directory path."""
    share_dir = Path(".kimi")
    share_dir.mkdir(parents=True, exist_ok=True)
    return share_dir
