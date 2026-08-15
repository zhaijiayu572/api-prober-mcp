"""Secure local storage primitives for user data."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from api_prober_mcp.errors import ProberError


class RuntimePaths:
    """Own the on-disk layout and reject unsafe files/symlinks."""

    def __init__(self, home: Path | None = None) -> None:
        self.root = home or Path(os.environ.get("API_PROBER_HOME", Path.home() / ".api-prober-mcp"))
        self.credentials = self.root / "credentials"
        self.profiles = self.credentials / "profiles"
        self.logs = self.root / "logs"
        self.cache = self.root / "cache" / "responses"

    def ensure_directories(self) -> None:
        for directory in (self.root, self.credentials, self.profiles, self.logs, self.cache):
            self._secure_directory(directory)

    def _secure_directory(self, path: Path) -> None:
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                raise ProberError(
                    "STORAGE_PERMISSION_INVALID",
                    "Storage path is not a real directory.",
                    {"path": str(path)},
                )
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise ProberError(
                    "STORAGE_PERMISSION_INVALID",
                    "Storage directory permissions are too broad.",
                    {"path": str(path), "expected": "0700"},
                    "Change the directory mode to 0700 and restart the MCP session.",
                )
            return
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(path, 0o700)

    def require_safe_file(self, path: Path) -> None:
        if not path.exists():
            return
        if path.is_symlink() or not path.is_file():
            raise ProberError(
                "STORAGE_PERMISSION_INVALID",
                "Storage file is not a regular file.",
                {"path": str(path)},
            )
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ProberError(
                "STORAGE_PERMISSION_INVALID",
                "Storage file permissions are too broad.",
                {"path": str(path), "expected": "0600"},
                "Change the file mode to 0600 and restart the MCP session.",
            )

    def config_path(self) -> Path:
        return self.root / "config.json"

    def profile_path(self, profile_id: str) -> Path:
        return self.profiles / f"{profile_id}.json"

    def profile_lock_path(self, profile_id: str) -> Path:
        return self.profiles / f"{profile_id}.lock"

    def log_path(self, day: str, session_id: str) -> Path:
        path = self.logs / day
        self._secure_directory(path)
        return path / f"{session_id}.jsonl"

    def session_cache_directory(self, session_id: str) -> Path:
        path = self.cache / session_id
        self._secure_directory(path)
        return path


def load_json(path: Path) -> dict[str, Any]:
    """Read a restricted JSON object without following a symlink."""
    if path.is_symlink() or not path.is_file():
        raise ProberError(
            "STORAGE_PERMISSION_INVALID", "JSON file is not a regular file.", {"path": str(path)}
        )
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProberError(
            "STORAGE_ERROR", "Unable to read JSON storage.", {"path": str(path)}
        ) from exc
    if not isinstance(value, dict):
        raise ProberError("STORAGE_ERROR", "Stored JSON must be an object.", {"path": str(path)})
    return value


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Write a 0600 JSON file by atomic same-directory replacement."""
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ProberError(
            "STORAGE_PERMISSION_INVALID", "Storage parent is unsafe.", {"path": str(parent)}
        )
    if path.exists() and path.is_symlink():
        raise ProberError(
            "STORAGE_PERMISSION_INVALID", "Refusing to replace a symlink.", {"path": str(path)}
        )
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=parent
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ProberError(
            "STORAGE_ERROR", "Unable to write local storage.", {"path": str(path)}
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
