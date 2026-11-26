"""Comprehensive Security, Encryption, and Isolation Tests for Credential Vault (M2.2)."""

from uuid import uuid4

import asyncpg
import pytest

from app.core.config import Settings
from app.core.database import close_db_pool, get_pool, init_db_pool
from app.modules.credentials.crypto import (
    build_payload_aad,
    build_wrapping_aad,
    compute_fingerprint,
    decrypt_payload,
    encrypt_payload,
    generate_dek,
    generate_nonce,
    mask_credential,
)
from app.modules.credentials.errors import (
    VaultAuthorizationError,
    VaultConfigurationError,
    VaultDuplicateError,
    VaultTamperError,
)
from app.modules.credentials.kms import LocalHKDFKEKProvider
from app.modules.credentials.repository import (
    delete_credential_secret,
    get_credential_metadata,
    retrieve_decrypted_credential,
    rewrap_credential_dek,
    store_credential,
)
from app.modules.credentials.vault import (
    close_vault_pool,
    get_vault_pool,
    init_vault_pool,
)


@pytest.fixture(autouse=True)
async def setup_pools():
    """Ensure both application pool and dedicated vault pool are initialized and cleaned up."""
    await init_db_pool()
    await init_vault_pool()
    yield
    await close_vault_pool()
    await close_db_pool()


@pytest.fixture
async def test_owner():
    """Create a verified test user profile to serve as credential owner."""
    pool = get_pool()
    user_id = uuid4()
    email = f"contributor_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Test Contributor"}'::jsonb)
            """,
            user_id,
            email,
        )

    yield {"user_id": user_id, "email": email}

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.api_key_metadata WHERE owner_id = $1", user_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1", user_id)


@pytest.fixture
async def admin_user():
    """Create an administrator profile to test that admins cannot decrypt credentials."""
    pool = get_pool()
    user_id = uuid4()
    email = f"admin_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Admin Tester"}'::jsonb)
            """,
            user_id,
            email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = 'admin', approval_status = 'approved'
            WHERE id = $1
            """,
            user_id,
        )

    yield {"user_id": user_id, "email": email}

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM auth.users WHERE id = $1", user_id)


@pytest.fixture
async def second_owner():
    """Create a second test user profile for cross-owner isolation testing."""
    pool = get_pool()
    user_id = uuid4()
    email = f"second_user_{user_id.hex[:8]}@university.edu"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Second Contributor"}'::jsonb)
            """,
            user_id,
            email,
        )

    yield {"user_id": user_id, "email": email}

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.api_key_metadata WHERE owner_id = $1", user_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1", user_id)


# ------------------------------------------------------------------------------
# 1. Cryptographic Primitive & Envelope Encryption Tests
# ------------------------------------------------------------------------------
def test_unique_dek_and_nonce_generation():
    """Every generated DEK and nonce must be cryptographically distinct."""
    deks = {generate_dek() for _ in range(100)}
    assert len(deks) == 100
    for dek in deks:
        assert len(dek) == 32

    nonces = {generate_nonce() for _ in range(100)}
    assert len(nonces) == 100
    for nonce in nonces:
        assert len(nonce) == 12


def test_encryption_decryption_round_trip():
    """Payload encryption and decryption round-trip succeeds with exact match."""
    raw_key = "gsk_live_test_api_key_9876543210abcdef"
    dek = generate_dek()
    owner_id = uuid4()
    cred_id = uuid4()
    payload_aad = build_payload_aad(owner_id, cred_id, "groq", 1)

    ciphertext, nonce = encrypt_payload(raw_key, dek, payload_aad)
    decrypted = decrypt_payload(ciphertext, nonce, dek, payload_aad)
    assert decrypted == raw_key


def test_ciphertext_tampering_fails():
    """Tampering with a single bit in the ciphertext causes decryption to fail closed."""
    raw_key = "gsk_live_test_api_key_sample"
    dek = generate_dek()
    payload_aad = build_payload_aad(uuid4(), uuid4(), "groq", 1)

    ciphertext, nonce = encrypt_payload(raw_key, dek, payload_aad)

    # Flip 1 byte in ciphertext
    tampered = bytearray(ciphertext)
    tampered[0] ^= 0x01

    with pytest.raises(VaultTamperError) as exc_info:
        decrypt_payload(bytes(tampered), nonce, dek, payload_aad)
    assert "Payload authentication failed" in str(exc_info.value)


def test_payload_aad_tampering_fails():
    """Decryption fails if any AAD context field differs from the encryption context."""
    raw_key = "sk-ant-test-credential"
    dek = generate_dek()
    owner_id = uuid4()
    cred_id = uuid4()
    correct_aad = build_payload_aad(owner_id, cred_id, "gemini", 1)
    tampered_aad = build_payload_aad(uuid4(), cred_id, "gemini", 1)  # Different owner

    ciphertext, nonce = encrypt_payload(raw_key, dek, correct_aad)

    with pytest.raises(VaultTamperError):
        decrypt_payload(ciphertext, nonce, dek, tampered_aad)


def test_separate_payload_and_wrapping_aad_contexts():
    """Payload AAD and Wrapping AAD have strictly distinct context representations."""
    owner_id = uuid4()
    cred_id = uuid4()
    provider_id = "openai"

    p_aad = build_payload_aad(owner_id, cred_id, provider_id, data_encryption_version=1)
    w_aad = build_wrapping_aad(
        owner_id, cred_id, provider_id, kek_version=1, wrapping_context_version=1
    )

    assert p_aad != w_aad
    assert p_aad.startswith(b"payload:")
    assert w_aad.startswith(b"wrap:")


def test_kek_provider_dek_wrapping_and_unwrapping():
    """KEK provider wraps DEK under AES-256-GCM and unwraps accurately."""
    master_key = "test-super-secret-master-key-that-is-at-least-32-chars!"
    kek_provider = LocalHKDFKEKProvider(master_key)
    dek = generate_dek()
    wrapping_aad = build_wrapping_aad(uuid4(), uuid4(), "cerebras", 1)

    enc_dek, nonce, auth_tag = kek_provider.wrap_dek(dek, wrapping_aad, 1)
    assert len(enc_dek) == 32
    assert len(nonce) == 12
    assert len(auth_tag) == 16

    unwrapped = kek_provider.unwrap_dek(enc_dek, nonce, auth_tag, wrapping_aad, 1)
    assert unwrapped == dek


def test_kek_unwrapping_with_tampered_tag_fails():
    """Tampering with DEK auth tag fails closed."""
    kek_provider = LocalHKDFKEKProvider("test-super-secret-master-key-that-is-at-least-32-chars!")
    dek = generate_dek()
    wrapping_aad = build_wrapping_aad(uuid4(), uuid4(), "cerebras", 1)

    enc_dek, nonce, auth_tag = kek_provider.wrap_dek(dek, wrapping_aad, 1)
    tampered_tag = bytearray(auth_tag)
    tampered_tag[0] ^= 0xFF

    with pytest.raises(VaultTamperError) as exc_info:
        kek_provider.unwrap_dek(enc_dek, nonce, bytes(tampered_tag), wrapping_aad, 1)
    assert "DEK authentication failed" in str(exc_info.value)


def test_kek_unwrapping_with_wrong_wrapping_aad_fails():
    """Unwrapping DEK with altered wrapping AAD fails closed."""
    kek_provider = LocalHKDFKEKProvider("test-super-secret-master-key-that-is-at-least-32-chars!")
    dek = generate_dek()
    cred_id = uuid4()
    owner_id = uuid4()
    wrapping_aad = build_wrapping_aad(owner_id, cred_id, "cerebras", kek_version=1)
    wrong_aad = build_wrapping_aad(owner_id, cred_id, "cerebras", kek_version=2)

    enc_dek, nonce, auth_tag = kek_provider.wrap_dek(dek, wrapping_aad, 1)

    with pytest.raises(VaultTamperError):
        kek_provider.unwrap_dek(enc_dek, nonce, auth_tag, wrong_aad, 1)


def test_kek_unwrapping_with_wrong_master_key_fails():
    """Unwrapping a DEK with a different master key fails closed."""
    provider1 = LocalHKDFKEKProvider("master-key-one-32-characters-minimum!!")
    provider2 = LocalHKDFKEKProvider("master-key-two-32-characters-minimum!!")
    dek = generate_dek()
    wrapping_aad = build_wrapping_aad(uuid4(), uuid4(), "openai", 1)

    enc_dek, nonce, auth_tag = provider1.wrap_dek(dek, wrapping_aad, 1)

    with pytest.raises(VaultTamperError):
        provider2.unwrap_dek(enc_dek, nonce, auth_tag, wrapping_aad, 1)


def test_kek_provider_rejects_short_master_key():
    """Master key shorter than 32 decoded bytes is rejected immediately."""
    with pytest.raises(VaultConfigurationError):
        LocalHKDFKEKProvider("short-key")


def test_fingerprint_consistency_and_pepper_isolation():
    """HMAC fingerprint is consistent, strips whitespace, and differs with pepper."""
    pepper1 = "pepper-one-32-characters-minimum-length!"
    pepper2 = "pepper-two-32-characters-minimum-length!"
    raw_key = "  sk-live-123456789  "

    fp1 = compute_fingerprint(raw_key, pepper1)
    fp1_clean = compute_fingerprint("sk-live-123456789", pepper1)
    assert fp1 == fp1_clean

    fp2 = compute_fingerprint(raw_key, pepper2)
    assert fp1 != fp2

    with pytest.raises(VaultConfigurationError):
        compute_fingerprint(raw_key, "too-short")


def test_mask_credential():
    """Credential masking preserves structure without disclosing secret."""
    assert mask_credential("gsk_1234567890abcdef") == "gsk_...cdef"
    assert mask_credential("shortkey") == "sh...ey"


# ------------------------------------------------------------------------------
# 2. Vault Repository & Database Isolation Tests
# ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_store_and_retrieve_credential_round_trip(test_owner):
    """Store raw credential in vault and decrypt it back successfully."""
    owner_id = test_owner["user_id"]
    raw_key = "gsk_live_secret_key_to_be_stored_in_vault"

    stored = await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="My Research Groq Key",
        raw_key=raw_key,
        sharing_scope="owner_only",
    )
    assert stored.owner_id == owner_id
    assert stored.provider_id == "groq"
    assert stored.masked_key == mask_credential(raw_key)
    assert stored.status == "active"
    assert stored.data_encryption_version == 1
    assert stored.kek_version == 1

    # Decrypt credential
    decrypted = await retrieve_decrypted_credential(
        credential_id=stored.id,
        caller_id=owner_id,
    )
    assert decrypted.id == stored.id
    assert decrypted.raw_key == raw_key
    assert decrypted.provider_id == "groq"
    assert decrypted.owner_id == owner_id
    assert decrypted.data_encryption_version == 1
    assert decrypted.kek_version == 1


@pytest.mark.anyio
async def test_cross_owner_isolation_full_matrix(test_owner, second_owner):
    """Owner B cannot decrypt, delete, or inspect Owner A's credential."""
    owner_a = test_owner["user_id"]
    owner_b = second_owner["user_id"]
    raw_key = "gsk_owner_a_confidential_key"

    stored = await store_credential(
        owner_id=owner_a,
        provider_id="groq",
        nickname="Owner A Key",
        raw_key=raw_key,
    )

    # 1. Owner B cannot decrypt
    with pytest.raises(VaultAuthorizationError) as exc_info:
        await retrieve_decrypted_credential(credential_id=stored.id, caller_id=owner_b)
    assert "Caller is not authorized" in str(exc_info.value)

    # 2. Owner B cannot delete
    with pytest.raises(VaultAuthorizationError) as exc_info:
        await delete_credential_secret(credential_id=stored.id, caller_id=owner_b)
    assert "Caller is not authorized" in str(exc_info.value)

    # 3. Owner B cannot view metadata
    with pytest.raises(VaultAuthorizationError) as exc_info:
        await get_credential_metadata(credential_id=stored.id, caller_id=owner_b)
    assert "Caller is not authorized" in str(exc_info.value)

    # Owner A can view metadata
    meta = await get_credential_metadata(credential_id=stored.id, caller_id=owner_a)
    assert meta.id == stored.id
    assert meta.owner_id == owner_a


@pytest.mark.anyio
async def test_duplicate_credential_fingerprint_rejected(test_owner):
    """Attempting to store the same key twice for the same provider raises VaultDuplicateError."""
    owner_id = test_owner["user_id"]
    raw_key = "gsk_duplicate_test_key_sample"

    await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="First Key",
        raw_key=raw_key,
    )

    with pytest.raises(VaultDuplicateError):
        await store_credential(
            owner_id=owner_id,
            provider_id="groq",
            nickname="Second Key",
            raw_key=raw_key,
        )


@pytest.mark.anyio
async def test_inactive_credential_cannot_be_decrypted(test_owner):
    """Disabled or revoked credentials fail decryption when check_active is True."""
    owner_id = test_owner["user_id"]
    raw_key = "gsk_inactive_test_key"

    stored = await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="Key To Disable",
        raw_key=raw_key,
    )

    # Disable credential in metadata
    pool = get_vault_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET status = 'disabled' WHERE id = $1",
            stored.id,
        )

    with pytest.raises(VaultAuthorizationError) as exc_info:
        await retrieve_decrypted_credential(
            credential_id=stored.id,
            caller_id=owner_id,
            check_active=True,
        )
    assert "not active" in str(exc_info.value)


@pytest.mark.anyio
async def test_dek_rewrap_preserves_ciphertext_and_nonce(test_owner):
    """Rewrapping DEK preserves the exact ciphertext and nonce without re-encrypting payload."""
    owner_id = test_owner["user_id"]
    raw_key = "gsk_rotation_test_key_material"

    stored = await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="Key For Pure DEK Rewrap",
        raw_key=raw_key,
        kek_version=1,
    )

    # Snapshot private secret columns before rotation
    pool = get_vault_pool()
    async with pool.acquire() as conn:
        row_before = await conn.fetchrow(
            "SELECT ciphertext, nonce, encrypted_dek, kek_version FROM private.api_key_secrets WHERE id = $1",
            stored.id,
        )

    # Rewrap DEK to kek_version = 2
    await rewrap_credential_dek(credential_id=stored.id, new_key_version=2)

    # Snapshot private secret columns after rotation
    async with pool.acquire() as conn:
        row_after = await conn.fetchrow(
            "SELECT ciphertext, nonce, encrypted_dek, kek_version FROM private.api_key_secrets WHERE id = $1",
            stored.id,
        )

    # CRITICAL INVARIANT: Ciphertext and nonce must be BITWISE IDENTICAL
    assert row_before["ciphertext"] == row_after["ciphertext"]
    assert row_before["nonce"] == row_after["nonce"]
    # Wrapped DEK and KEK version must be updated
    assert row_before["encrypted_dek"] != row_after["encrypted_dek"]
    assert row_after["kek_version"] == 2

    # Decrypt and verify raw key is perfectly restored
    decrypted = await retrieve_decrypted_credential(
        credential_id=stored.id,
        caller_id=owner_id,
    )
    assert decrypted.raw_key == raw_key
    assert decrypted.kek_version == 2


@pytest.mark.anyio
async def test_admin_cannot_decrypt_contributor_credentials(test_owner, admin_user):
    """Platform administrators are strictly prohibited from decrypting contributor secrets."""
    owner_id = test_owner["user_id"]
    admin_id = admin_user["user_id"]
    raw_key = "gsk_contributor_private_secret_key"

    stored = await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="Contributor Research Key",
        raw_key=raw_key,
    )

    # 1. Admin attempting to decrypt must raise VaultAuthorizationError
    with pytest.raises(VaultAuthorizationError) as exc_info:
        await retrieve_decrypted_credential(credential_id=stored.id, caller_id=admin_id)
    assert "Caller is not authorized" in str(exc_info.value)

    # 2. Admin attempting to delete contributor credential must raise VaultAuthorizationError
    with pytest.raises(VaultAuthorizationError) as exc_info:
        await delete_credential_secret(credential_id=stored.id, caller_id=admin_id)
    assert "Caller is not authorized" in str(exc_info.value)


@pytest.mark.anyio
async def test_repeated_key_rotation_multi_version(test_owner):
    """Multiple sequential KEK rotations (v1 -> v2 -> v3) succeed seamlessly.

    Invariants verified:
    1. Ciphertext and nonce remain 100% bitwise identical across all KEK versions.
    2. Wrapped DEK and kek_version are updated at each step.
    3. Decryption using the rotated master key perfectly restores the original raw key.
    """
    owner_id = test_owner["user_id"]
    raw_key = "gsk_multi_rotation_key"

    stored = await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="Multi-Rotation Key",
        raw_key=raw_key,
        kek_version=1,
    )

    pool = get_vault_pool()
    async with pool.acquire() as conn:
        row_v1 = await conn.fetchrow(
            "SELECT ciphertext, nonce, encrypted_dek, kek_version FROM private.api_key_secrets WHERE id = $1",
            stored.id,
        )

    # 1. Rotate v1 -> v2
    await rewrap_credential_dek(credential_id=stored.id, new_kek_version=2)
    async with pool.acquire() as conn:
        row_v2 = await conn.fetchrow(
            "SELECT ciphertext, nonce, encrypted_dek, kek_version FROM private.api_key_secrets WHERE id = $1",
            stored.id,
        )
    # Ciphertext and nonce MUST remain unchanged
    assert row_v2["ciphertext"] == row_v1["ciphertext"]
    assert row_v2["nonce"] == row_v1["nonce"]
    assert row_v2["encrypted_dek"] != row_v1["encrypted_dek"]
    assert row_v2["kek_version"] == 2

    dec2 = await retrieve_decrypted_credential(credential_id=stored.id, caller_id=owner_id)
    assert dec2.raw_key == raw_key
    assert dec2.kek_version == 2

    # 2. Rotate v2 -> v3
    await rewrap_credential_dek(credential_id=stored.id, new_kek_version=3)
    async with pool.acquire() as conn:
        row_v3 = await conn.fetchrow(
            "SELECT ciphertext, nonce, encrypted_dek, kek_version FROM private.api_key_secrets WHERE id = $1",
            stored.id,
        )
    # Ciphertext and nonce MUST still remain unchanged from original v1
    assert row_v3["ciphertext"] == row_v1["ciphertext"]
    assert row_v3["nonce"] == row_v1["nonce"]
    assert row_v3["encrypted_dek"] != row_v2["encrypted_dek"]
    assert row_v3["kek_version"] == 3

    dec3 = await retrieve_decrypted_credential(credential_id=stored.id, caller_id=owner_id)
    assert dec3.raw_key == raw_key
    assert dec3.kek_version == 3


@pytest.mark.anyio
async def test_decrypted_credential_repr_masks_plaintext_key(test_owner):
    """The __repr__ of DecryptedCredential strictly excludes the raw plaintext key."""
    owner_id = test_owner["user_id"]
    raw_key = "gsk_super_confidential_raw_key_material"

    stored = await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="Key For Repr Test",
        raw_key=raw_key,
    )

    decrypted = await retrieve_decrypted_credential(
        credential_id=stored.id,
        caller_id=owner_id,
    )
    repr_str = repr(decrypted)
    assert raw_key not in repr_str
    assert "gsk_super_confidential" not in repr_str
    assert decrypted.masked_key in repr_str


@pytest.mark.anyio
async def test_delete_credential_cascades_and_purges_secret(test_owner):
    """Deleting credential removes public metadata and cascades to private.api_key_secrets."""
    owner_id = test_owner["user_id"]
    raw_key = "gsk_deletion_test_key"

    stored = await store_credential(
        owner_id=owner_id,
        provider_id="groq",
        nickname="Key To Delete",
        raw_key=raw_key,
    )

    await delete_credential_secret(credential_id=stored.id, caller_id=owner_id)

    # Verify secret row is gone
    pool = get_vault_pool()
    async with pool.acquire() as conn:
        secret_row = await conn.fetchrow(
            "SELECT id FROM private.api_key_secrets WHERE id = $1", stored.id
        )
        assert secret_row is None

        meta_row = await conn.fetchrow(
            "SELECT id FROM public.api_key_metadata WHERE id = $1", stored.id
        )
        assert meta_row is None


@pytest.mark.anyio
async def test_ordinary_app_role_cannot_access_private_secrets():
    """An authenticated application role is denied all access to private.api_key_secrets."""
    app_pool = get_pool()

    async with app_pool.acquire() as conn:
        # Check 1: SELECT denied
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch("SELECT * FROM private.api_key_secrets")

        # Check 2: INSERT denied
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("INSERT INTO private.api_key_secrets (id) VALUES ($1)", uuid4())


@pytest.mark.anyio
async def test_dedicated_vault_pool_role_and_least_privilege():
    """The vault pool authenticates as ragbait_vault_user and inherits ragbait_vault_service."""
    app_pool = get_pool()
    vault_pool = get_vault_pool()

    assert app_pool is not vault_pool

    async with vault_pool.acquire() as conn:
        current_user = await conn.fetchval("SELECT current_user")
        assert current_user == "ragbait_vault_user"

        is_member = await conn.fetchval(
            "SELECT pg_has_role('ragbait_vault_user', 'ragbait_vault_service', 'MEMBER')"
        )
        assert is_member is True

        # Verify bypassrls is disabled
        bypass_rls = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'ragbait_vault_user'"
        )
        assert bypass_rls is False

        # Verify denied access to public.profiles
        async with conn.transaction():
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch("SELECT * FROM public.profiles")

        # Verify denied access to public.audit_logs
        async with conn.transaction():
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch("SELECT * FROM public.audit_logs")


# ------------------------------------------------------------------------------
# 3. Production Configuration & Hardening Tests
# ------------------------------------------------------------------------------
def test_config_staging_production_rejection_of_dev_defaults():
    """Staging and production reject development keys, equal keys, and local KMS."""
    # 1. Dev default master key in staging rejected
    with pytest.raises(ValueError, match="Development default VAULT_MASTER_KEY"):
        Settings(
            ENVIRONMENT="staging",
            VAULT_MASTER_KEY="dev-master-vault-kek-secret-key-32chars!",
            VAULT_PEPPER_SECRET="secure-prod-pepper-secret-32-chars-long!",
        )

    # 2. Equal master key and pepper rejected
    with pytest.raises(ValueError, match="must be distinct"):
        Settings(
            ENVIRONMENT="development",
            VAULT_MASTER_KEY="identical-secret-key-at-least-32-chars-long",
            VAULT_PEPPER_SECRET="identical-secret-key-at-least-32-chars-long",
        )

    # 3. Keys shorter than 32 bytes rejected
    with pytest.raises(ValueError, match="at least 32 bytes"):
        Settings(
            ENVIRONMENT="development",
            VAULT_MASTER_KEY="short-key",
            VAULT_PEPPER_SECRET="dev-vault-fingerprint-pepper-secret-32chars!",
        )

    # 4. Local KMS in production rejected
    with pytest.raises(
        ValueError, match="LocalHKDFKEKProvider is strictly prohibited in production"
    ):
        Settings(
            ENVIRONMENT="production",
            VAULT_MASTER_KEY="secure-prod-master-key-32-characters!",
            VAULT_PEPPER_SECRET="secure-prod-pepper-secret-32-chars-long!",
            KMS_TYPE="local_hkdf",
            VAULT_DATABASE_URL="postgresql://vault_user:strong_password@prod:5432/postgres",
            SUPABASE_ANON_KEY="production-real-anon-key-at-least-32-chars-long",
            SUPABASE_SERVICE_ROLE_KEY="production-real-service-key-at-least-32-chars",
            SUPABASE_JWT_SECRET="production-real-jwt-secret-at-least-32-chars-long",
        )


def test_local_hkdf_prohibited_in_production(monkeypatch):
    """LocalHKDFKEKProvider raises VaultConfigurationError when instantiated in production."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    with pytest.raises(VaultConfigurationError) as exc_info:
        LocalHKDFKEKProvider("secure-prod-master-key-32-characters!")
    assert "strictly prohibited in production" in str(exc_info.value)


# ------------------------------------------------------------------------------
# 4. Cross-Account Duplicacy & Bypass Prevention Tests
# ------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_cross_account_duplicate_key_blocked_even_across_providers_and_invisible_chars(
    test_owner, second_owner
):
    """Two accounts cannot register the same API key, even with invisible chars or a different provider."""
    owner_a = test_owner["user_id"]
    owner_b = second_owner["user_id"]
    raw_key = f"sk-or-v1-cross-acct-{uuid4().hex}"

    await store_credential(
        owner_id=owner_a,
        provider_id="openrouter",
        nickname="Owner A OpenRouter Key",
        raw_key=raw_key,
    )

    # Same key submitted by Owner B for same provider -> blocked
    with pytest.raises(VaultDuplicateError):
        await store_credential(
            owner_id=owner_b,
            provider_id="openrouter",
            nickname="Owner B Stolen Key",
            raw_key=raw_key,
        )

    # Same key padded with invisible formatting characters -> blocked
    with pytest.raises(VaultDuplicateError):
        await store_credential(
            owner_id=owner_b,
            provider_id="openrouter",
            nickname="Owner B Padded Key",
            raw_key=f" \u200b{raw_key}\ufeff ",
        )

    # Same key submitted by Owner B under a different provider -> blocked
    with pytest.raises(VaultDuplicateError):
        await store_credential(
            owner_id=owner_b,
            provider_id="groq",
            nickname="Owner B Cross-Provider Attempt",
            raw_key=raw_key,
        )


@pytest.mark.anyio
async def test_cross_account_duplicate_key_blocked_after_original_owner_deletes(
    test_owner, second_owner
):
    """Deleting a key from Owner A does not free its fingerprint for Owner B to bypass the gate."""
    owner_a = test_owner["user_id"]
    owner_b = second_owner["user_id"]
    raw_key = f"sk-or-v1-delete-reuse-{uuid4().hex}"

    stored = await store_credential(
        owner_id=owner_a,
        provider_id="openrouter",
        nickname="Owner A Original Key",
        raw_key=raw_key,
    )

    # Owner A deletes their credential
    await delete_credential_secret(credential_id=stored.id, caller_id=owner_a)

    # Owner B STILL cannot register the exact same key
    with pytest.raises(VaultDuplicateError):
        await store_credential(
            owner_id=owner_b,
            provider_id="openrouter",
            nickname="Owner B Gate Bypass Key",
            raw_key=raw_key,
        )

    # Original Owner A can legitimately re-add their own previously deleted key
    readded = await store_credential(
        owner_id=owner_a,
        provider_id="openrouter",
        nickname="Owner A Re-added Key",
        raw_key=raw_key,
    )
    assert readded.owner_id == owner_a
