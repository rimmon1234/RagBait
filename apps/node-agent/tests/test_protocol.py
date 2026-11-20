"""Tests for Node Agent protocol messages serialization and parsing."""

from ragbait_node.protocol import (
    WSAuthMessage,
    WSChallengeMessage,
    WSHeartbeatMessage,
    WSJobChunkMessage,
    WSJobCompletedMessage,
    WSJobDispatchMessage,
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


def test_config_allowed_models(tmp_path):
    from ragbait_node.config import NodeConfig

    cfg = NodeConfig(node_id="n1", allowed_models=["llama3:8b", "mistral:7b"], config_dir=tmp_path)
    cfg.save()
    loaded = NodeConfig.load(tmp_path)
    assert loaded.allowed_models == ["llama3:8b", "mistral:7b"]


async def test_client_model_allowlist_rejection(tmp_path):
    from unittest.mock import AsyncMock, patch
    from ragbait_node.client import NodeClient
    from ragbait_node.config import NodeConfig

    cfg = NodeConfig(
        node_id="n1",
        server_public_key="0" * 64,
        allowed_models=["llama3:8b"],
        config_dir=tmp_path,
    )
    client = NodeClient(cfg)
    mock_ws = AsyncMock()

    with patch("ragbait_node.crypto.verify_server_job_authorization", return_value=True):
        msg = WSJobDispatchMessage(
            job_id="job-unauthorized",
            authorization={"model_name": "phi3:mini"},
            payload={"model": "phi3:mini", "messages": []},
        )
        await client._handle_job_dispatch(mock_ws, msg)
        mock_ws.send.assert_not_called()
