import os
import shutil
import sys
from pathlib import Path


APP_FOLDER_NAME = "TimetableWebApp"


def _bundle_root():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def _runtime_root():
    if getattr(sys, "frozen", False):
        base_dir = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        runtime_dir = base_dir / APP_FOLDER_NAME
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return runtime_dir
    return Path(__file__).resolve().parent


BUNDLE_ROOT = _bundle_root()
RUNTIME_ROOT = _runtime_root()


def resource_path(*parts):
    return BUNDLE_ROOT.joinpath(*parts)


def data_path(*parts):
    path = RUNTIME_ROOT.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_runtime_copy(filename):
    source = resource_path(filename)
    destination = data_path(filename)

    if destination.exists() or not source.exists():
        return destination

    if source == destination:
        return destination

    shutil.copy2(source, destination)
    return destination
