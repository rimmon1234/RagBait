"""Tests for Node Agent protocol messages serialization and parsing."""

from ragbait_node.protocol import (
    WSAuthMessage,
    WSChallengeMessage,
    WSHeartbeatMessage,
    WSJobChunkMessage,
    WSJobCompletedMessage,
)


def test_protocol_frames():
    challenge = WSChallengeMessage(nonce="abc", session_id="s1", timestamp=123)
    assert challenge.type == "challenge"

    auth = WSAuthMessage(node_id="n1", signature="sig", timestamp=123)
    assert auth.type == "auth"

    hb = WSHeartbeatMessage(installed_models=[{"name": "llama3.2:3b"}], active_jobs=1)
    assert hb.type == "heartbeat"
    assert len(hb.installed_models) == 1

    chunk = WSJobChunkMessage(job_id="j1", seq_id=1, delta="hello", done=False)
    assert chunk.type == "job_chunk"
    assert chunk.delta == "hello"

    comp = WSJobCompletedMessage(
        job_id="j1",
        reported_input_tokens=10,
        reported_output_tokens=20,
        duration_ms=500,
        tokens_per_second=40.0,
    )
    assert comp.type == "job_completed"
    assert comp.tokens_per_second == 40.0
