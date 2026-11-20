"""Cryptographic functions for Community Node authentication and job authorization."""

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.core.config import settings

logger = logging.getLogger("ragbait.community.crypto")


def compute_payload_digest(payload: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hex digest of a JSON-serializable request payload."""
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def build_challenge_canonical_string(
    node_id: str | UUID,
    nonce_hex: str,
    session_id: str | UUID,
    timestamp: int,
) -> bytes:
    """Build deterministic canonical byte payload for node WebSocket handshake authentication."""
    canonical = f"RAGBAIT-NODE-AUTH:v1:{node_id}:{nonce_hex}:{session_id}:{timestamp}"
    return canonical.encode("utf-8")


def verify_node_challenge_signature(
    signature_hex: str,
    node_id: str | UUID,
    nonce_hex: str,
    session_id: str | UUID,
    timestamp: int,
    node_public_key_hex: str,
) -> bool:
    """Verify Ed25519 signature of an authentication challenge from a node agent."""
    try:
        sig_bytes = bytes.fromhex(signature_hex)
        pub_bytes = bytes.fromhex(node_public_key_hex)
        if len(sig_bytes) != 64 or len(pub_bytes) != 32:
            return False

        public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        canonical_bytes = build_challenge_canonical_string(
            node_id=node_id,
            nonce_hex=nonce_hex,
            session_id=session_id,
            timestamp=timestamp,
        )
        public_key.verify(sig_bytes, canonical_bytes)
        return True
    except (ValueError, InvalidSignature, TypeError) as e:
        logger.debug(f"Challenge signature verification failed: {e}")
        return False


def build_job_authorization_canonical_string(
    job_id: str | UUID,
    node_id: str | UUID,
    model_name: str,
    payload_digest: str,
    max_output_tokens: int,
    hard_deadline_ts: int,
    auth_nonce: str,
) -> bytes:
    """Build deterministic canonical byte payload for server-signed job authorization."""
    canonical = (
        f"RAGBAIT-JOB-AUTH:v1:{job_id}:{node_id}:{model_name}:{payload_digest}:"
        f"{max_output_tokens}:{hard_deadline_ts}:{auth_nonce}"
    )
    return canonical.encode("utf-8")


def sign_job_authorization(
    job_id: str | UUID,
    node_id: str | UUID,
    model_name: str,
    payload_digest: str,
    max_output_tokens: int,
    hard_deadline_ts: int,
    auth_nonce: str,
) -> str:
    """Sign a job authorization using the server's Ed25519 private key."""
    priv_bytes = bytes.fromhex(settings.COMMUNITY_SERVER_SIGNING_KEY_HEX)
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)

    canonical_bytes = build_job_authorization_canonical_string(
        job_id=job_id,
        node_id=node_id,
        model_name=model_name,
        payload_digest=payload_digest,
        max_output_tokens=max_output_tokens,
        hard_deadline_ts=hard_deadline_ts,
        auth_nonce=auth_nonce,
    )
    signature = private_key.sign(canonical_bytes)
    return signature.hex()


def verify_job_authorization(
    signature_hex: str,
    job_id: str | UUID,
    node_id: str | UUID,
    model_name: str,
    payload_digest: str,
    max_output_tokens: int,
    hard_deadline_ts: int,
    auth_nonce: str,
    server_public_key_hex: str | None = None,
) -> bool:
    """Verify server Ed25519 signature on a job authorization."""
    try:
        sig_bytes = bytes.fromhex(signature_hex)
        pub_hex = server_public_key_hex or settings.COMMUNITY_SERVER_VERIFYING_KEY_HEX
        pub_bytes = bytes.fromhex(pub_hex)
        if len(sig_bytes) != 64 or len(pub_bytes) != 32:
            return False

        public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        canonical_bytes = build_job_authorization_canonical_string(
            job_id=job_id,
            node_id=node_id,
            model_name=model_name,
            payload_digest=payload_digest,
            max_output_tokens=max_output_tokens,
            hard_deadline_ts=hard_deadline_ts,
            auth_nonce=auth_nonce,
        )
        public_key.verify(sig_bytes, canonical_bytes)
        return True
    except (ValueError, InvalidSignature, TypeError) as e:
        logger.debug(f"Job authorization signature verification failed: {e}")
        return False
