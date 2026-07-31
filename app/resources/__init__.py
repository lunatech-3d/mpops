"""Packaged application resources."""

from pathlib import Path
import sys


def resource_path(name: str) -> Path:
    """Return a resource in source checkouts and PyInstaller bundles."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / "app" / "resources" / name
    return Path(__file__).resolve().parent / name
