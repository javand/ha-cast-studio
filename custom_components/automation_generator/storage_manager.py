"""Storage manager for scratchpad cleanup, overwrite, and retention pruning."""

import shutil
import time
from pathlib import Path

from .const import DEFAULT_OUTPUT_DIR, LOGGER

DEFAULT_SCRATCH_BASE = "/tmp/automation_generator"  # noqa: S108


def get_routine_scratch_dir(
    routine_name: str,
    base_tmp: str | None = None,
) -> str:
    """Get and create scratch directory for a routine."""
    target_base = Path(base_tmp) if base_tmp is not None else Path(DEFAULT_SCRATCH_BASE)
    scratch_dir = target_base / routine_name
    scratch_dir.mkdir(parents=True, exist_ok=True)
    return str(scratch_dir)


def cleanup_scratch_dir(
    routine_name: str,
    base_tmp: str | None = None,
) -> None:
    """Clean up scratch directory recursively for a routine."""
    target_base = Path(base_tmp) if base_tmp is not None else Path(DEFAULT_SCRATCH_BASE)
    scratch_dir = target_base / routine_name
    if scratch_dir.exists():
        try:
            if scratch_dir.is_symlink() or scratch_dir.is_file():
                scratch_dir.unlink()
            else:
                shutil.rmtree(scratch_dir)
        except OSError as err:
            LOGGER.warning("Failed to clean up scratch dir %s: %s", scratch_dir, err)


def prune_stale_files(
    directory: str | None = None,
    max_age_seconds: int = 86400,
) -> list[str]:
    """Remove files in directory older than max_age_seconds."""
    target_dir = Path(directory) if directory is not None else Path(DEFAULT_OUTPUT_DIR)
    if not target_dir.is_dir():
        return []

    pruned: list[str] = []
    now = time.time()
    for file_path in target_dir.rglob("*"):
        if file_path.is_file():
            try:
                mtime = file_path.stat().st_mtime
                if now - mtime > max_age_seconds:
                    file_path.unlink()
                    pruned.append(str(file_path))
            except OSError as err:
                LOGGER.warning("Failed to prune file %s: %s", file_path, err)
    return pruned
