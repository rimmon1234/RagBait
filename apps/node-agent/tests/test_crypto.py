"""Tests for Node Agent cryptography, signing, and signature verification."""

import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ragbait_node import crypto


def test_keypair_generation_and_loading(tmp_path: Path):
    key_path = tmp_path / "node_key.pem"
    priv1 = crypto.get_or_create_keypair(key_path)
    assert key_path.exists()
    pub_hex1 = crypto.get_public_key_hex(priv1)
    assert len(pub_hex1) == 64

    # Load existing
    priv2 = crypto.get_or_create_keypair(key_path)
    pub_hex2 = crypto.get_public_key_hex(priv2)
    assert pub_hex1 == pub_hex2


def test_sign_challenge():
    priv = ed25519.Ed25519PrivateKey.generate()
    sig_hex = crypto.sign_challenge(
        private_key=priv,
        node_id="test-node-1",
        nonce="0" * 64,
        session_id="sess-123",
        timestamp=1700000000,
    )
    assert len(sig_hex) == 128  # 64 bytes in hex

    sig_bytes = bytes.fromhex(sig_hex)
    canonical = b"RAGBAIT-NODE-AUTH:v1:test-node-1:" + (b"0" * 64) + b":sess-123:1700000000"
    priv.public_key().verify(sig_bytes, canonical)


def test_verify_server_job_authorization():
    server_priv = ed25519.Ed25519PrivateKey.generate()
    server_pub_hex = server_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    job_id = "job-abc-123"
    node_id = "node-xyz-789"
    model_name = "llama3.2:3b"
    payload = {"model": "llama3.2:3b", "messages": [{"role": "user", "content": "hi"}]}
    digest = crypto.compute_payload_digest(payload)
    max_output = 4096
    deadline = int(time.time()) + 300
    nonce = "nonce-123"

    canonical = f"RAGBAIT-JOB-AUTH:v1:{job_id}:{node_id}:{model_name}:{digest}:{max_output}:{deadline}:{nonce}".encode()
    sig = server_priv.sign(canonical).hex()

    auth = {
        "job_id": job_id,
        "node_id": node_id,
        "model_name": model_name,
        "payload_digest": digest,
        "max_output_tokens": max_output,
        "hard_deadline_ts": deadline,
        "auth_nonce": nonce,
        "signature": sig,
    }

    # 1. Valid authorization
    assert crypto.verify_server_job_authorization(auth, payload, server_pub_hex, node_id) is True

    # 2. Tampered payload
    tampered_payload = {"model": "llama3.2:3b", "messages": [{"role": "user", "content": "attack"}]}
    assert (
        crypto.verify_server_job_authorization(auth, tampered_payload, server_pub_hex, node_id)
        is False
    )

    # 3. Wrong node ID
    assert (
        crypto.verify_server_job_authorization(auth, payload, server_pub_hex, "different-node")
        is False
    )

    # 4. Expired deadline
    expired_auth = dict(auth)
    expired_auth["hard_deadline_ts"] = int(time.time()) - 10
    assert (
        crypto.verify_server_job_authorization(expired_auth, payload, server_pub_hex, node_id)
        is False
    )
