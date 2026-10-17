"""Tests for storage manager (scratchpad cleanup, overwrite, and retention pruning)."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

from custom_components.automation_generator.const import DEFAULT_OUTPUT_DIR
from custom_components.automation_generator.storage_manager import (
    DEFAULT_SCRATCH_BASE,
    cleanup_scratch_dir,
    get_routine_scratch_dir,
    prune_stale_files,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_scratch_dir_lifecycle(tmp_path: Path):
    """Test creating and cleaning up routine scratch directory."""
    scratch_dir = get_routine_scratch_dir("morning", base_tmp=str(tmp_path))
    assert os.path.exists(scratch_dir)
    test_file = os.path.join(scratch_dir, "clip_01.mp4")
    with open(test_file, "w") as f:
        f.write("dummy clip")
    assert os.path.exists(test_file)

    cleanup_scratch_dir("morning", base_tmp=str(tmp_path))
    assert not os.path.exists(scratch_dir)


def test_cleanup_nonexistent_scratch_dir(tmp_path: Path):
    """Test cleanup does not raise when directory does not exist."""
    cleanup_scratch_dir("nonexistent_routine", base_tmp=str(tmp_path))


def test_prune_stale_files(tmp_path: Path):
    """Test pruning files older than max_age_seconds."""
    old_file = tmp_path / "old_routine.mp4"
    new_file = tmp_path / "new_routine.mp4"
    old_file.write_text("old")
    new_file.write_text("new")

    # Backdate old_file by 25 hours (90,000s > 86,400s)
    past_time = time.time() - 90000
    os.utime(str(old_file), (past_time, past_time))

    pruned = prune_stale_files(str(tmp_path), max_age_seconds=86400)
    assert str(old_file) in pruned
    assert not os.path.exists(str(old_file))
    assert os.path.exists(str(new_file))


def test_prune_nested_stale_files(tmp_path: Path):
    """Test pruning files in subdirectories."""
    nested_dir = tmp_path / "sub"
    nested_dir.mkdir()
    old_nested = nested_dir / "old_clip.mp4"
    old_nested.write_text("old clip")

    past_time = time.time() - 100000
    os.utime(str(old_nested), (past_time, past_time))

    pruned = prune_stale_files(str(tmp_path), max_age_seconds=86400)
    assert str(old_nested) in pruned
    assert not os.path.exists(str(old_nested))


def test_prune_nonexistent_directory(tmp_path: Path):
    """Test pruning returns empty list when directory does not exist."""
    missing_dir = str(tmp_path / "missing")
    assert prune_stale_files(missing_dir) == []


def test_default_paths(tmp_path: Path):
    """Test default paths and constants."""
    assert DEFAULT_SCRATCH_BASE == "/tmp/automation_generator"  # noqa: S108
    assert DEFAULT_OUTPUT_DIR == "/config/www/automations"

    # Verify default directory for prune_stale_files points to DEFAULT_OUTPUT_DIR
    with patch(
        "custom_components.automation_generator.storage_manager.DEFAULT_OUTPUT_DIR",
        str(tmp_path),
    ):
        file = tmp_path / "test.mp4"
        file.write_text("content")
        past_time = time.time() - 100000
        os.utime(str(file), (past_time, past_time))
        pruned = prune_stale_files()
        assert str(file) in pruned
