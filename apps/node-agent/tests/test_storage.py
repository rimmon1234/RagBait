"""Tests for Node Agent SQLite metadata-only deduplication storage."""

from pathlib import Path

from ragbait_node.storage import StateStorage


def test_state_storage_deduplication(tmp_path: Path):
    db_path = tmp_path / "state.db"
    store = StateStorage(db_path)

    assert store.has_job("job-1") is False

    store.record_job("job-1", "llama3.2:3b", "accepted")
    assert store.has_job("job-1") is True

    # Prune old jobs (none older than 7 days)
    pruned = store.prune_old_jobs(retention_days=7)
    assert pruned == 0
    assert store.has_job("job-1") is True
