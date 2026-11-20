"""Ed25519 cryptographic identity and verification for the Node Agent."""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

logger = logging.getLogger("ragbait.node.crypto")


def compute_payload_digest(payload: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hex digest of a JSON-serializable request payload."""
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def get_or_create_keypair(key_path: Path) -> ed25519.Ed25519PrivateKey:
    """Load existing Ed25519 private key or generate a new one with 0600 permissions."""
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        with open(key_path, "rb") as f:
            key_data = f.read()
        loaded = serialization.load_pem_private_key(key_data, password=None)
        if not isinstance(loaded, ed25519.Ed25519PrivateKey):
            raise ValueError("Existing key file is not an Ed25519 private key")
        return loaded

    # Generate fresh Ed25519 private key
    private_key = ed25519.Ed25519PrivateKey.generate()
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    with open(key_path, "wb") as f:
        f.write(pem_bytes)

    # Restrict permissions on POSIX systems
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass  # Windows file security handled by user profile ACLs

    return private_key


def get_public_key_hex(private_key: ed25519.Ed25519PrivateKey) -> str:
    """Return raw 32-byte public key as 64-character lowercase hex string."""
    pub = private_key.public_key()
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return pub_bytes.hex()


def sign_challenge(
    private_key: ed25519.Ed25519PrivateKey,
    node_id: str,
    nonce: str,
    session_id: str,
    timestamp: int,
) -> str:
    """Sign WebSocket authentication challenge using local Ed25519 private key."""
    canonical = f"RAGBAIT-NODE-AUTH:v1:{node_id}:{nonce}:{session_id}:{timestamp}".encode()
    signature = private_key.sign(canonical)
    return signature.hex()


def verify_server_job_authorization(
    auth: dict[str, Any],
    payload: dict[str, Any],
    server_public_key_hex: str,
    expected_node_id: str,
) -> bool:
    """Verify that a job authorization was authentically signed by RagBait server."""
    try:
        # 1. Verify target node ID matches self
        if str(auth.get("node_id")) != expected_node_id:
            logger.warning(
                f"Rejected job intended for node {auth.get('node_id')} (self: {expected_node_id})"
            )
            return False

        # 2. Verify deadline has not passed
        deadline_ts = int(auth.get("hard_deadline_ts", 0))
        if deadline_ts <= int(time.time()):
            logger.warning("Rejected expired job authorization")
            return False

        # 3. Verify payload digest matches actual received payload
        calculated_digest = compute_payload_digest(payload)
        claimed_digest = auth.get("payload_digest", "")
        if calculated_digest != claimed_digest:
            logger.warning("Rejected job with mismatched payload digest")
            return False

        # 4. Verify cryptographic signature
        sig_hex = auth.get("signature", "")
        sig_bytes = bytes.fromhex(sig_hex)
        pub_bytes = bytes.fromhex(server_public_key_hex)
        if len(sig_bytes) != 64 or len(pub_bytes) != 32:
            return False

        server_public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        canonical = (
            f"RAGBAIT-JOB-AUTH:v1:{auth.get('job_id')}:{auth.get('node_id')}:"
            f"{auth.get('model_name')}:{claimed_digest}:{auth.get('max_output_tokens')}:"
            f"{deadline_ts}:{auth.get('auth_nonce')}"
        ).encode()

        server_public_key.verify(sig_bytes, canonical)
        return True
    except (ValueError, InvalidSignature, TypeError) as e:
        logger.warning(f"Job authorization signature verification failed: {e}")
        return False
