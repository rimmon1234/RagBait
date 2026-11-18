"""Tests for M2.1 Provider/Model Registry and Dynamic Quotas."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.core.database import close_db_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.llm_gateway.service import get_model, resolve_model_identity
from app.modules.users.schemas import ApprovalStatus, UserProfile, UserRole
from tests.conftest import create_test_jwt, make_test_profile


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()
    await close_db_pool()
    await close_redis()


def auth_headers(
    user_id=None,
    email="student@university.edu",
    role: UserRole = "user",
    status: ApprovalStatus = "approved",
) -> tuple[dict[str, str], UserProfile]:
    uid = user_id or uuid4()
    profile = make_test_profile(user_id=uid, email=email, role=role, approval_status=status)
    token = create_test_jwt(user_id=uid, email=email)

    app.dependency_overrides[get_current_profile] = lambda: profile
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=uid,
        email=email,
        role="authenticated",
        session_id=str(uuid4()),
        claims={},
    )
    return {"Authorization": f"Bearer {token}"}, profile


@pytest.mark.anyio
async def test_list_providers_authenticated(client: httpx.AsyncClient):
    """Authenticated users can list enabled providers."""
    headers, _ = auth_headers()

    response = await client.get("/api/v1/llm/providers", headers=headers)
    assert response.status_code == 200
    providers = response.json()
    assert len(providers) >= 6

    provider_ids = [p["id"] for p in providers]
    assert "gemini" in provider_ids
    assert "groq" in provider_ids
    assert "openrouter" in provider_ids
    assert "cerebras" in provider_ids
    assert "openai" in provider_ids
    assert "ollama" in provider_ids

    # Verify separated account_tier and billing_status
    groq_provider = next(p for p in providers if p["id"] == "groq")
    assert groq_provider["runtime_type"] == "cloud_api"
    assert groq_provider["requires_account_scope"] is True
    assert groq_provider["account_tier"] == "free"
    assert groq_provider["billing_status"] == "active"


@pytest.mark.anyio
async def test_list_active_models_and_catalogue_integrity(client: httpx.AsyncClient):
    """Active models are returned; retired models are absent; preview/alias flags verified."""
    headers, _ = auth_headers()

    response = await client.get("/api/v1/llm/models", headers=headers)
    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    model_ids = [m["id"] for m in items]

    # Verify retired models are NOT in active catalogue
    assert "gemini-1.5-flash" not in model_ids
    assert "gemini-1.5-pro" not in model_ids
    assert "gemini-1.5-flash-8b" not in model_ids
    assert "gemini-2.0-flash-exp" not in model_ids
    assert "llama-3.3-70b-versatile" not in model_ids
    assert "llama-3.1-8b-instant" not in model_ids

    # Verify current supported models are present
    assert "gemini-3.8-flash" in model_ids
    assert "gemini-3.5-flash-lite" in model_ids
    assert "openai/gpt-oss-120b" in model_ids
    assert "openai/gpt-oss-20b" in model_ids
    assert "qwen/qwen3.8-27b" in model_ids
    assert "openrouter/free" in model_ids
    assert "cerebras/gpt-oss-120b" in model_ids

    # Requirement 1: qwen/qwen3.8-27b must have release_stage = 'preview'
    qwen_model = next(m for m in items if m["id"] == "qwen/qwen3.8-27b")
    assert qwen_model["release_stage"] == "preview"
    assert qwen_model["provider_id"] == "groq"

    # Requirement 3: openrouter/free must have is_routing_alias = True and alias_target = None
    router_model = next(m for m in items if m["id"] == "openrouter/free")
    assert router_model["is_routing_alias"] is True
    assert router_model["alias_target"] is None

    # Separate concrete free model must also exist
    concrete_free = next(
        m for m in items if m["id"] == "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    )
    assert concrete_free["is_routing_alias"] is False
    assert concrete_free["alias_target"] is None
    assert concrete_free["free_tier_eligible"] is True


@pytest.mark.anyio
async def test_get_model_details(client: httpx.AsyncClient):
    """Retrieve details for a single model and test 404 for missing."""
    headers, _ = auth_headers()

    # Valid model
    response = await client.get("/api/v1/llm/models/gemini-3.8-flash", headers=headers)
    assert response.status_code == 200
    model = response.json()
    assert model["id"] == "gemini-3.8-flash"
    assert model["context_window"] == 1048576
    assert model["max_output_tokens"] == 8192
    assert "vision" in model["capabilities"]

    # Nonexistent model
    bad_res = await client.get("/api/v1/llm/models/non-existent-model", headers=headers)
    assert bad_res.status_code == 404
    assert bad_res.json()["error"]["code"] == "MODEL_NOT_FOUND"


@pytest.mark.anyio
async def test_list_active_quotas_provenance(client: httpx.AsyncClient):
    """Dynamic quotas are returned with full provenance and confidence tracking."""
    headers, _ = auth_headers()

    response = await client.get("/api/v1/llm/quotas", headers=headers)
    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    assert len(items) >= 5

    for q in items:
        assert q["confidence"] in ("high", "medium", "low")
        assert q["provenance"] in (
            "account_confirmed",
            "provider_published",
            "observed",
            "estimated",
            "unknown",
        )
        assert q["is_active"] is True


@pytest.mark.anyio
async def test_admin_permissions_enforcement(client: httpx.AsyncClient):
    """Non-admin users cannot access admin registry endpoints."""
    headers, _ = auth_headers(role="user")

    # Try listing admin models
    res = await client.get("/api/v1/admin/models", headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ADMIN_REQUIRED"

    # Try registering model
    create_payload = {
        "id": "custom-test-model",
        "provider_id": "groq",
        "family": "custom",
        "display_name": "Custom Model",
        "context_window": 32768,
        "max_output_tokens": 4096,
    }
    res2 = await client.post("/api/v1/admin/models", json=create_payload, headers=headers)
    assert res2.status_code == 403


@pytest.fixture
async def db_admin():
    from app.core.database import get_pool

    pool = get_pool()
    admin_id = uuid4()
    email = f"admin_{admin_id.hex[:8]}@example.edu"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Admin User"}'::jsonb)
            """,
            admin_id,
            email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = 'admin', approval_status = 'approved'
            WHERE id = $1
            """,
            admin_id,
        )

    headers, profile = auth_headers(user_id=admin_id, email=email, role="admin", status="approved")
    yield {"headers": headers, "profile": profile, "admin_id": admin_id}

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM public.audit_logs WHERE actor_id = $1", admin_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1", admin_id)
        await conn.execute("DELETE FROM public.llm_models WHERE id LIKE 'test-model-%'")
        await conn.execute("DELETE FROM public.provider_quotas WHERE scope_id LIKE 'org_test_%'")


@pytest.mark.anyio
async def test_admin_model_lifecycle_crud(client: httpx.AsyncClient, db_admin):
    """Admin can register, deactivate, deprecate with replacement, and shut down a model."""
    headers = db_admin["headers"]
    test_model_id = f"test-model-{uuid4().hex[:8]}"

    # 1. Register new model
    create_payload = {
        "id": test_model_id,
        "provider_id": "groq",
        "family": "test-family",
        "display_name": "Test Lifecycle Model",
        "release_stage": "ga",
        "is_routing_alias": False,
        "free_tier_eligible": True,
        "input_cost_per_million_cents": 10,
        "output_cost_per_million_cents": 25,
        "capabilities": ["streaming", "tools"],
        "context_window": 65536,
        "max_output_tokens": 4096,
        "modalities_input": ["text"],
        "modalities_output": ["text"],
        "source_documentation": "https://example.com/test",
    }
    res = await client.post("/api/v1/admin/models", json=create_payload, headers=headers)
    assert res.status_code == 201
    model = res.json()
    assert model["id"] == test_model_id
    assert model["availability_status"] == "active"
    assert model["is_active"] is True

    # 2. Deactivate model (disabled)
    patch_res = await client.patch(
        f"/api/v1/admin/models/{test_model_id}",
        json={"availability_status": "disabled"},
        headers=headers,
    )
    assert patch_res.status_code == 200
    disabled_model = patch_res.json()
    assert disabled_model["availability_status"] == "disabled"
    assert disabled_model["is_active"] is False

    # Verify disabled model is excluded from default public active listing
    public_res = await client.get("/api/v1/llm/models", headers=headers)
    public_model_ids = [m["id"] for m in public_res.json()["items"]]
    assert test_model_id not in public_model_ids

    # 3. Deprecate model and assign replacement
    deprecate_res = await client.patch(
        f"/api/v1/admin/models/{test_model_id}",
        json={
            "availability_status": "deprecated",
            "replacement_model_id": "openai/gpt-oss-120b",
        },
        headers=headers,
    )
    assert deprecate_res.status_code == 200
    dep_model = deprecate_res.json()
    assert dep_model["availability_status"] == "deprecated"
    assert dep_model["replacement_model_id"] == "openai/gpt-oss-120b"

    # 4. Shut down model
    now_iso = datetime.now(UTC).isoformat()
    shutdown_res = await client.patch(
        f"/api/v1/admin/models/{test_model_id}",
        json={
            "availability_status": "shutdown",
            "shutdown_at": now_iso,
        },
        headers=headers,
    )
    assert shutdown_res.status_code == 200
    shut_model = shutdown_res.json()
    assert shut_model["availability_status"] == "shutdown"
    assert shut_model["shutdown_at"] is not None


@pytest.mark.anyio
async def test_admin_dynamic_quota_crud(client: httpx.AsyncClient, db_admin):
    """Admin can create and update dynamic quotas with explicit provenance."""
    headers = db_admin["headers"]
    unique_scope = f"org_test_{uuid4().hex[:6]}"

    # Create quota rule
    quota_payload = {
        "provider_id": "groq",
        "scope_type": "organization",
        "scope_id": unique_scope,
        "model_id": None,
        "limit_type": "rpm",
        "limit_value": 45,
        "source_documentation": "Custom university grant",
        "confidence": "high",
        "provenance": "account_confirmed",
        "is_active": True,
    }
    create_res = await client.post(
        "/api/v1/admin/provider-quotas", json=quota_payload, headers=headers
    )
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["limit_value"] == 45
    assert created["provenance"] == "account_confirmed"
    quota_id = created["id"]

    # Update quota rule
    update_res = await client.patch(
        f"/api/v1/admin/provider-quotas/{quota_id}",
        json={"limit_value": 60, "confidence": "high"},
        headers=headers,
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["limit_value"] == 60


@pytest.mark.anyio
async def test_admin_sync_provider(client: httpx.AsyncClient, db_admin):
    """Admin can trigger provider discovery sync."""
    headers = db_admin["headers"]

    res = await client.post("/api/v1/admin/providers/groq/sync", headers=headers)
    assert res.status_code == 200
    sync_report = res.json()
    assert sync_report["provider_id"] == "groq"
    assert sync_report["models_discovered"] >= 3
    assert sync_report["models_active"] >= 1


@pytest.mark.anyio
async def test_dynamic_alias_resolution_helper(client: httpx.AsyncClient):
    """Dynamic aliases capture upstream model from response; standard models remain fixed."""
    headers, _ = auth_headers()

    # 1. OpenRouter dynamic routing alias
    router_model = await get_model("openrouter/free")
    resolved = resolve_model_identity(
        model=router_model,
        provider_response_model="meta-llama/llama-3.3-70b-instruct:free",
        upstream_provider="openrouter",
    )
    assert resolved.requested_model_id == "openrouter/free"
    assert resolved.is_routing_alias is True
    assert resolved.resolved_model_id == "meta-llama/llama-3.3-70b-instruct:free"
    assert resolved.upstream_provider == "openrouter"

    # 2. Concrete standard model
    concrete_model = await get_model("gemini-3.8-flash")
    standard_res = resolve_model_identity(model=concrete_model)
    assert standard_res.requested_model_id == "gemini-3.8-flash"
    assert standard_res.is_routing_alias is False
    assert standard_res.resolved_model_id == "gemini-3.8-flash"
    assert standard_res.upstream_provider == "gemini"


@pytest.mark.anyio
async def test_safe_discovery_quarantines_new_models_and_preserves_metadata(
    client: httpx.AsyncClient, db_admin
):
    """Discovery adds new models as disabled and does not overwrite admin configurations."""
    headers = db_admin["headers"]
    unverified_id = f"groq/test-unverified-{uuid4().hex[:6]}"

    # Sync with a newly discovered model
    sync_payload = {
        "discovered_models": [
            {
                "id": unverified_id,
                "family": "test-family",
                "display_name": "Discovered Test Model",
                "capabilities": ["streaming"],
                "context_window": 8192,
                "max_output_tokens": 2048,
            }
        ]
    }
    sync_res = await client.post(
        "/api/v1/admin/providers/groq/sync", json=sync_payload, headers=headers
    )
    assert sync_res.status_code == 200
    report = sync_res.json()
    assert unverified_id in report["unverified_discovered"]

    # Verify newly discovered model is quarantined (disabled) and absent from public active list
    public_res = await client.get("/api/v1/llm/models", headers=headers)
    active_ids = [m["id"] for m in public_res.json()["items"]]
    assert unverified_id not in active_ids

    # Verify model in admin list is disabled
    admin_model = await get_model(unverified_id)
    assert admin_model.availability_status == "disabled"
    assert admin_model.is_active is False

    # Customize admin metadata
    patch_res = await client.patch(
        f"/api/v1/admin/models/{unverified_id}",
        json={"display_name": "Admin Curated Name"},
        headers=headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["display_name"] == "Admin Curated Name"

    # Re-run sync with discovery payload attempting to overwrite display_name
    re_sync = await client.post(
        "/api/v1/admin/providers/groq/sync",
        json={
            "discovered_models": [
                {
                    "id": unverified_id,
                    "display_name": "Attempted Discovery Overwrite Name",
                }
            ]
        },
        headers=headers,
    )
    assert re_sync.status_code == 200

    # Ensure admin curated metadata was NOT overwritten
    refreshed = await get_model(unverified_id)
    assert refreshed.display_name == "Admin Curated Name"
    assert refreshed.availability_status == "disabled"

    # Clean up test model
    from app.core.database import get_pool

    async with get_pool().acquire() as conn:
        await conn.execute("DELETE FROM public.llm_models WHERE id = $1", unverified_id)


@pytest.mark.anyio
async def test_suspended_user_blocked_from_gateway(client: httpx.AsyncClient):
    """Suspended users cannot query gateway endpoints."""
    headers, _ = auth_headers(status="suspended")

    res = await client.get("/api/v1/llm/providers", headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACCOUNT_SUSPENDED"
