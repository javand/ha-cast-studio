"""Storage manager for scratchpad cleanup, overwrite, and retention pruning."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .const import DEFAULT_OUTPUT_DIR, LOGGER

DEFAULT_SCRATCH_BASE = "/tmp/cast_studio"  # noqa: S108


class InvalidRoutineNameError(ValueError):
    """Raised when a routine name is invalid or attempts directory traversal."""


def _is_safe_subpath(target_path: Path, base_path: Path) -> bool:
    """Return True if target_path is strictly a child of base_path."""
    resolved_base = base_path.resolve()
    resolved_target = target_path.resolve()
    return resolved_target != resolved_base and resolved_target.is_relative_to(
        resolved_base
    )


def get_routine_scratch_dir(
    routine_name: str,
    base_tmp: str | None = None,
) -> str:
    """Get and create scratch directory for a routine."""
    clean_routine = routine_name.strip() if routine_name else ""
    if not clean_routine:
        msg = "routine_name cannot be empty or whitespace"
        raise InvalidRoutineNameError(msg)

    target_base = Path(base_tmp) if base_tmp is not None else Path(DEFAULT_SCRATCH_BASE)
    scratch_dir = target_base / clean_routine

    if not _is_safe_subpath(scratch_dir, target_base):
        msg = f"Invalid routine_name path traversal: '{routine_name}'"
        raise InvalidRoutineNameError(msg)

    resolved_scratch = scratch_dir.resolve()
    resolved_scratch.mkdir(parents=True, exist_ok=True)
    return str(resolved_scratch)


def cleanup_scratch_dir(
    routine_name: str,
    base_tmp: str | None = None,
) -> None:
    """Clean up scratch directory recursively for a routine."""
    clean_routine = routine_name.strip() if routine_name else ""
    if not clean_routine:
        return

    target_base = Path(base_tmp) if base_tmp is not None else Path(DEFAULT_SCRATCH_BASE)
    scratch_dir = target_base / clean_routine

    if not _is_safe_subpath(scratch_dir, target_base):
        LOGGER.warning(
            "Invalid scratch directory path '%s' outside base '%s'",
            routine_name,
            target_base,
        )
        return

    resolved_scratch = scratch_dir.resolve()
    if resolved_scratch.exists():
        try:
            if resolved_scratch.is_symlink() or resolved_scratch.is_file():
                resolved_scratch.unlink()
            else:
                shutil.rmtree(resolved_scratch)
        except OSError as err:
            LOGGER.warning(
                "Failed to clean up scratch dir %s: %s",
                resolved_scratch,
                err,
            )


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
                if file_path.is_relative_to(target_dir / "static"):
                    continue
            except (ValueError, AttributeError):
                pass
            try:
                mtime = file_path.stat().st_mtime
                if now - mtime > max_age_seconds:
                    file_path.unlink()
                    pruned.append(str(file_path))
            except OSError as err:
                LOGGER.warning("Failed to prune file %s: %s", file_path, err)
    return pruned

