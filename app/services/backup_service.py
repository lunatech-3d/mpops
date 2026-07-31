"""Safe SQLite online backups and their application settings/history."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.security.audit import record_event


logger = logging.getLogger(__name__)
TIMESTAMPED_PATTERN = "mpops_backup_*.db"


@dataclass(frozen=True)
class BackupResult:
    filename: str
    destination: Path
    completed_at: datetime
    file_size: int
    integrity_result: str


class BackupService:
    def __init__(self, auth):
        self.auth = auth

    def get_setting(self, key: str, default: str = "") -> str:
        with self.auth.connection() as connection:
            row = connection.execute(
                "SELECT setting_value FROM AppSettings WHERE setting_key = ?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str, user_id: int) -> None:
        if self.auth.settings.reporting_copy:
            raise PermissionError("Settings cannot be changed in reporting mode.")
        with self.auth.connection() as connection:
            connection.execute(
                "INSERT INTO AppSettings(setting_key, setting_value, updated_at, updated_by) "
                "VALUES (?, ?, CURRENT_TIMESTAMP, ?) ON CONFLICT(setting_key) DO UPDATE SET "
                "setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP, updated_by=excluded.updated_by",
                (key, value, user_id),
            )

    def backup_folder(self) -> Path | None:
        value = self.get_setting("backup_folder")
        return Path(value).expanduser() if value else None

    def configure_folder(self, folder: Path, user_id: int) -> None:
        self._validate_folder(folder)
        self.set_setting("backup_folder", str(folder.resolve()), user_id)

    def _validate_folder(self, folder: Path) -> None:
        source = self.auth.settings.database_path.resolve()
        folder = folder.expanduser().resolve()
        if not folder.is_dir():
            raise ValueError("The selected backup folder does not exist.")
        if folder == source.parent:
            raise ValueError("Choose a folder different from the live database folder.")
        if not os.access(folder, os.W_OK):
            raise ValueError("The selected backup folder is not writable.")
        try:
            handle, probe = tempfile.mkstemp(prefix=".mpops-write-test-", dir=folder)
            os.close(handle)
            Path(probe).unlink()
        except OSError as exc:
            raise ValueError("The selected backup folder is not writable.") from exc

    def create_backup(self, user_id: int, progress=None) -> BackupResult:
        if self.auth.settings.reporting_copy:
            raise PermissionError("A reporting copy cannot be used as a backup source.")
        folder = self.backup_folder()
        completed_at = datetime.now().astimezone()
        integrity = "not run"
        destination = None
        temp_path = None
        filename = f"mpops_backup_{completed_at.strftime('%Y-%m-%d_%H%M%S')}.db"
        try:
            if folder is None:
                raise ValueError("Select a backup folder before starting a backup.")
            self._validate_folder(folder)
            if progress:
                progress("Creating database snapshot...")
            handle, raw_path = tempfile.mkstemp(prefix="mpops-snapshot-", suffix=".db")
            os.close(handle)
            temp_path = Path(raw_path)
            with self.auth.connect() as source, sqlite3.connect(temp_path) as snapshot:
                source.backup(snapshot)
            if progress:
                progress("Verifying backup...")
            with sqlite3.connect(f"file:{temp_path.as_posix()}?mode=ro", uri=True) as check:
                integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"Integrity check returned: {integrity}")
            destination = folder / filename
            if destination.exists():
                raise FileExistsError("A backup with this timestamp already exists; please try again.")
            if progress:
                progress("Copying verified backup to the configured folder...")
            history_temp = folder / f".{filename}.{os.getpid()}.tmp"
            try:
                shutil.copy2(temp_path, history_temp)
                os.replace(history_temp, destination)
            finally:
                history_temp.unlink(missing_ok=True)
            latest_temp = folder / f".mpops_latest.{os.getpid()}.tmp"
            try:
                shutil.copy2(destination, latest_temp)
                os.replace(latest_temp, folder / "mpops_latest.db")
            finally:
                latest_temp.unlink(missing_ok=True)
            size = destination.stat().st_size
            completed_at = datetime.now().astimezone()
            self._record(completed_at, destination, filename, size, True, integrity, user_id, None)
            self._apply_retention(folder, destination)
            if progress:
                progress("Backup completed successfully.")
            return BackupResult(filename, destination, completed_at, size, integrity)
        except Exception as exc:
            logger.exception("Database backup failed")
            self._record(completed_at, destination, filename, None, False, integrity, user_id, str(exc))
            raise
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)

    def _record(self, when, destination, filename, size, succeeded, integrity, user_id, error):
        with self.auth.connection() as connection:
            connection.execute(
                "INSERT INTO BackupHistory(completed_at, source_database_path, destination_path, "
                "backup_filename, file_size, succeeded, integrity_result, initiated_by, error_message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (when.isoformat(timespec="seconds"), str(self.auth.settings.database_path.resolve()),
                 str(destination) if destination else None, filename, size, int(succeeded), integrity,
                 user_id, error),
            )
            record_event(connection, "database_backup_succeeded" if succeeded else "database_backup_failed",
                         actor_user_id=user_id, details={"filename": filename, "error": error})

    def _apply_retention(self, folder: Path, current: Path) -> None:
        try:
            keep = max(1, int(self.get_setting("backup_retention_count", "30")))
        except ValueError:
            keep = 30
        backups = sorted(
            (path for path in folder.glob(TIMESTAMPED_PATTERN)
             if path.is_file() and path.resolve() != self.auth.settings.database_path.resolve()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in backups[keep:]:
            if old != current:
                try:
                    old.unlink()
                except OSError:
                    logger.exception("Could not remove expired backup %s", old)

    def recent_history(self, limit: int = 12):
        with self.auth.connection() as connection:
            return connection.execute(
                "SELECT completed_at, backup_filename, file_size, succeeded, integrity_result, error_message "
                "FROM BackupHistory ORDER BY backup_id DESC LIMIT ?", (limit,)
            ).fetchall()

    def backed_up_today(self) -> bool:
        today = datetime.now().astimezone().date()
        for row in self.recent_history(100):
            if row[3] and datetime.fromisoformat(row[0]).astimezone().date() == today:
                return True
        return False
