from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_DATA_DIR = PROJECT_ROOT / "data"


def default_data_dir() -> Path:
    override = os.environ.get("TAOBAO_ASSISTANT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (Path(local_app_data) / "TaobaoAssistant").resolve()
    return (Path.home() / ".taobao-assistant").resolve()


DATA_DIR = default_data_dir()


def _copy_file_if_changed(source: str, destination: str) -> str:
    source_path = Path(source)
    destination_path = Path(destination)
    try:
        source_stat = source_path.stat()
        destination_stat = destination_path.stat()
        if (
            source_stat.st_size == destination_stat.st_size
            and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
        ):
            return str(destination_path)
    except OSError:
        pass
    return shutil.copy2(source_path, destination_path)


def migrate_legacy_data(legacy_dir: Path, target_dir: Path) -> bool:
    """Copy an old project-local database/profile set into the per-user data directory.

    The legacy directory is intentionally retained as a backup. Account profile
    paths are rebased only after their corresponding profile directory was copied.
    """
    legacy_dir = legacy_dir.resolve()
    target_dir = target_dir.resolve()
    legacy_db = legacy_dir / "taobao_assistant_v2.db"
    target_db = target_dir / "taobao_assistant_v2.db"
    marker = target_dir / "migration-from-project-data.txt"
    if legacy_dir == target_dir or not legacy_db.is_file():
        return False
    # A pre-existing independent user database must never be replaced. A marker
    # means this target was created by us and an interrupted profile copy may retry.
    if target_db.exists() and not marker.exists():
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    if not target_db.exists():
        temporary_db = target_db.with_suffix(".db.migrating")
        try:
            temporary_db.unlink(missing_ok=True)
            with closing(sqlite3.connect(legacy_db)) as source, closing(sqlite3.connect(temporary_db)) as destination:
                source.backup(destination)
            temporary_db.replace(target_db)
            changed = True
        finally:
            temporary_db.unlink(missing_ok=True)
        marker.write_text(
            f"Database copied from: {legacy_dir}\nProfile migration is in progress.\n",
            encoding="utf-8",
        )

    target_profiles = target_dir / "profiles"
    target_profiles.mkdir(parents=True, exist_ok=True)
    failed_profiles: list[str] = []
    with closing(sqlite3.connect(target_db)) as connection:
        rows = connection.execute("SELECT id,profile_dir FROM accounts").fetchall()
        with connection:
            for account_id, stored_profile in rows:
                source_profile = Path(str(stored_profile))
                try:
                    if source_profile.resolve().is_relative_to(target_profiles):
                        continue
                except OSError:
                    pass
                if not source_profile.is_dir():
                    source_profile = legacy_dir / "profiles" / source_profile.name
                destination_profile = target_profiles / source_profile.name
                if source_profile.is_dir():
                    try:
                        shutil.copytree(
                            source_profile,
                            destination_profile,
                            dirs_exist_ok=True,
                            copy_function=_copy_file_if_changed,
                        )
                    except OSError as exc:
                        # Keep the old absolute path if Chrome still has a file locked.
                        failed_profiles.append(f"{source_profile}: {exc}")
                        continue
                    connection.execute(
                        "UPDATE accounts SET profile_dir=? WHERE id=?",
                        (str(destination_profile), int(account_id)),
                    )
                    changed = True

    status = "Profile migration completed."
    if failed_profiles:
        status = "Some profiles are still using the old path and will retry on the next start:\n" + "\n".join(
            failed_profiles
        )
    marker.write_text(
        f"Database copied from: {legacy_dir}\n{status}\nThe original directory was retained as a backup.\n",
        encoding="utf-8",
    )
    return changed


def prepare_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    migrate_legacy_data(LEGACY_DATA_DIR, DATA_DIR)
    return DATA_DIR
