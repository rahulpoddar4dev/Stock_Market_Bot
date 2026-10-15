# drive_sync.py - Upload generated CSV files to Google Drive via rclone

from __future__ import annotations

import subprocess
import shutil
import threading
import time
from pathlib import Path

import config
from log_utils import log


BASE_DIR = Path(__file__).resolve().parent
ACTIVE_LOG_FILES = {
    Path("logs") / "trade_history.csv",
    Path("logs") / "paper_option_trades.csv",
    Path("logs") / "learning_history.csv",
    Path("logs") / "paper_trades.csv",
}


def _csv_roots() -> list[Path]:
    roots = []
    for root in getattr(config, "GOOGLE_DRIVE_SYNC_ROOTS", []):
        path = BASE_DIR / root
        if path.exists():
            roots.append(path)
    return roots


def discover_csv_files(include_active_logs: bool = False) -> list[Path]:
    files: list[Path] = []
    excluded_filenames = set(getattr(config, "GOOGLE_DRIVE_SYNC_EXCLUDE_FILENAMES", []))
    for root in _csv_roots():
        for path in root.rglob("*.csv"):
            if not path.is_file():
                continue
            if path.name in excluded_filenames:
                continue
            rel_path = path.relative_to(BASE_DIR)
            if not include_active_logs and rel_path in ACTIVE_LOG_FILES:
                continue
            files.append(path)
    return sorted(files)


def _remote_env_prefix() -> str:
    env = str(getattr(config, "BOT_ENV", "main") or "main").strip().lower()
    if env in {"", "main", "prod", "production"}:
        return ""
    return f"{env}/"


def _remote_path(path: Path, remote_name: str) -> str:
    rel_path = path.relative_to(BASE_DIR).as_posix()
    return f"{remote_name}:{_remote_env_prefix()}{rel_path}"


def _rclone_base_command() -> list[str]:
    return [
        "--drive-root-folder-id",
        getattr(config, "GOOGLE_DRIVE_FOLDER_ID", ""),
    ]


def _rclone_copy(path: Path, remote_name: str, folder_id: str) -> subprocess.CompletedProcess:
    command = [
        "rclone",
        "copyto",
        str(path),
        _remote_path(path, remote_name),
        *_rclone_base_command(),
    ]
    return subprocess.run(command, cwd=BASE_DIR, text=True, capture_output=True, check=False)


def rclone_available() -> bool:
    return shutil.which("rclone") is not None


def restore_csv_from_drive(path: Path) -> bool:
    if not rclone_available():
        return False

    remote_name = getattr(config, "GOOGLE_DRIVE_RCLONE_REMOTE", "tradingbotdrive")
    folder_id = getattr(config, "GOOGLE_DRIVE_FOLDER_ID", "")
    if not folder_id:
        return False

    try:
        rel_path = path.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == 0:
        path.unlink()
    command = [
        "rclone",
        "copyto",
        f"{remote_name}:{_remote_env_prefix()}{rel_path}",
        str(path),
        *_rclone_base_command(),
    ]
    completed = subprocess.run(command, cwd=BASE_DIR, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return False
    return path.exists()


def list_drive_trade_days() -> list[str]:
    if not rclone_available():
        return []

    remote_name = getattr(config, "GOOGLE_DRIVE_RCLONE_REMOTE", "tradingbotdrive")
    folder_id = getattr(config, "GOOGLE_DRIVE_FOLDER_ID", "")
    if not folder_id:
        return []

    command = [
        "rclone",
        "lsf",
        f"{remote_name}:{_remote_env_prefix()}logs/by_date",
        "--dirs-only",
        *_rclone_base_command(),
    ]
    completed = subprocess.run(command, cwd=BASE_DIR, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return []

    return sorted(
        [line.strip().rstrip("/") for line in completed.stdout.splitlines() if line.strip()],
        reverse=True,
    )


def sync_csv_to_drive(
    *,
    move_after_upload: bool | None = None,
    include_active_logs: bool | None = None,
    max_files: int | None = None,
) -> dict:
    remote_name = getattr(config, "GOOGLE_DRIVE_RCLONE_REMOTE", "tradingbotdrive")
    folder_id = getattr(config, "GOOGLE_DRIVE_FOLDER_ID", "")
    move_after_upload = (
        getattr(config, "GOOGLE_DRIVE_SYNC_MOVE_AFTER_UPLOAD", False)
        if move_after_upload is None
        else move_after_upload
    )
    include_active_logs = (
        getattr(config, "GOOGLE_DRIVE_SYNC_INCLUDE_ACTIVE_LOGS", False)
        if include_active_logs is None
        else include_active_logs
    )

    result = {
        "ok": False,
        "remote": remote_name,
        "folder_id": folder_id,
        "uploaded": 0,
        "moved": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    if not folder_id:
        result["errors"].append("GOOGLE_DRIVE_FOLDER_ID is not configured.")
        return result

    files = discover_csv_files(include_active_logs=include_active_logs)
    if max_files:
        files = files[:max_files]

    for path in files:
        completed = _rclone_copy(path, remote_name, folder_id)
        rel_path = path.relative_to(BASE_DIR).as_posix()
        if completed.returncode != 0:
            result["failed"] += 1
            error = (completed.stderr or completed.stdout or "Unknown rclone error").strip()
            result["errors"].append(f"{rel_path}: {error}")
            continue

        result["uploaded"] += 1
        if move_after_upload:
            path.unlink()
            result["moved"] += 1

    result["ok"] = result["failed"] == 0
    return result


def start_csv_drive_sync_background() -> threading.Thread | None:
    if not getattr(config, "GOOGLE_DRIVE_SYNC_ENABLED", False):
        return None

    if not rclone_available():
        log("Google Drive CSV sync disabled: rclone is not installed.")
        return None

    interval = int(getattr(config, "GOOGLE_DRIVE_SYNC_INTERVAL_SECONDS", 300) or 300)

    def _worker():
        while True:
            result = sync_csv_to_drive()
            if result["ok"]:
                log(
                    "Google Drive CSV sync OK: "
                    f"uploaded={result['uploaded']} moved={result['moved']}"
                )
            else:
                first_error = result["errors"][0] if result["errors"] else "Unknown error"
                log(
                    "Google Drive CSV sync failed: "
                    f"uploaded={result['uploaded']} failed={result['failed']} error={first_error}"
                )
            time.sleep(interval)

    thread = threading.Thread(target=_worker, name="csv-drive-sync", daemon=True)
    thread.start()
    return thread
