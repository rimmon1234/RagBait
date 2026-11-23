"""Comprehensive test suite for Phase 4 M4.1 Chat Persistence & Conversation Management."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import get_pool, init_db_pool
from app.core.redis import init_redis
from app.main import app
from app.modules.chat.service import ChatService
from app.modules.users.schemas import UserProfile
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []
CREATED_TEST_SUBJECTS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    await init_redis()
    yield
    app.dependency_overrides.clear()

    # Clean up test database records
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            if CREATED_TEST_USERS or CREATED_TEST_SUBJECTS:
                await conn.execute(
                    "DELETE FROM public.chat_conversations WHERE user_id = ANY($1::uuid[]) OR subject_id = ANY($2::uuid[])",
                    CREATED_TEST_USERS,
                    CREATED_TEST_SUBJECTS,
                )
            if CREATED_TEST_USERS:
                await conn.execute(
                    "DELETE FROM public.api_key_metadata WHERE owner_id = ANY($1::uuid[])",
                    CREATED_TEST_USERS,
                )
            if CREATED_TEST_SUBJECTS:
                await conn.execute(
                    "DELETE FROM public.kb_subjects WHERE id = ANY($1::uuid[])",
                    CREATED_TEST_SUBJECTS,
                )
            if CREATED_TEST_USERS:
                await conn.execute(
                    "DELETE FROM auth.users WHERE id = ANY($1::uuid[])",
                    CREATED_TEST_USERS,
                )
    except Exception:
        pass
    CREATED_TEST_USERS.clear()
    CREATED_TEST_SUBJECTS.clear()


async def add_qualifying_credential(user_id: UUID, provider_id: str = "groq") -> UUID:
    """Provision an active, valid, community-shared free-tier credential for testing."""
    cred_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent,
                sharing_consent_timestamp, consent_version
            )
            VALUES ($1, $2, $3, 'Test Qualifying Key', 'gsk_...test', $4, 'active', 'valid', 'community_shared', true, now(), 'v1');
            """,
            cred_id,
            user_id,
            provider_id,
            uuid4().hex[:16],
        )
    return cred_id


async def create_user_fixture(
    email_prefix: str,
    role: str = "user",
    approval_status: str = "approved",
    with_qualifying_key: bool = True,
) -> tuple[UUID, str, UserProfile]:
    """Create a verified test user in auth.users and public.profiles with optional contribution."""
    user_id = uuid4()
    email = f"{email_prefix}_{user_id.hex[:6]}@test.internal"
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"display_name":"Test User"}'::jsonb);
            """,
            user_id,
            email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = $1, approval_status = $2
            WHERE id = $3;
            """,
            role,
            approval_status,
            user_id,
        )

    CREATED_TEST_USERS.append(user_id)

    # Provision qualifying cloud credential if requested for approved non-admin user
    if (
        with_qualifying_key
        and approval_status == "approved"
        and role not in ("admin", "super_admin")
    ):
        await add_qualifying_credential(user_id)

    token = create_test_jwt(user_id, email=email)
    profile = make_test_profile(
        user_id=user_id, email=email, role=role, approval_status=approval_status
    )
    return user_id, token, profile


async def create_subject_fixture(name: str, slug: str, is_active: bool = True) -> UUID:
    """Create a test academic subject in public.kb_subjects."""
    subject_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
            VALUES ($1, $2, $3, 'Test subject description', $4);
            """,
            subject_id,
            name,
            slug,
            is_active,
        )
    CREATED_TEST_SUBJECTS.append(subject_id)
    return subject_id


# ==============================================================================
# 1. Authorization & Entitlement Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(client: httpx.AsyncClient):
    """Unauthenticated requests must return HTTP 401."""
    resp = await client.get("/api/v1/chat/conversations")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_TOKEN"


@pytest.mark.asyncio
async def test_unapproved_user_rejected(client: httpx.AsyncClient):
    """Pending, rejected, or suspended accounts must return HTTP 403."""
    _, pending_token, _ = await create_user_fixture("pending", approval_status="pending")
    resp = await client.get(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {pending_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_NOT_APPROVED"

    _, suspended_token, _ = await create_user_fixture("suspended", approval_status="suspended")
    resp = await client.get(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {suspended_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "ACCOUNT_SUSPENDED"


@pytest.mark.asyncio
async def test_non_contributor_create_conversation_rejected(client: httpx.AsyncClient):
    """Approved student WITHOUT a qualifying free-tier cloud key cannot start new conversations."""
    _, non_contrib_token, _ = await create_user_fixture(
        "non_contributor", with_qualifying_key=False
    )
    subject_id = await create_subject_fixture("Compiler Design", "cd-entitle")

    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "Lexical Analysis"},
        headers={"Authorization": f"Bearer {non_contrib_token}"},
    )
    assert resp.status_code == 403
    err = resp.json()["error"]
    assert err["code"] == "CHAT_CONTRIBUTION_REQUIRED"
    assert "qualifying" in err["message"].lower() or "contribute" in err["message"].lower()


@pytest.mark.asyncio
async def test_non_contributor_can_list_and_read_conversations(client: httpx.AsyncClient):
    """Approved student without current key CAN list and read their past conversations."""
    user_id, token, _ = await create_user_fixture("past_contributor", with_qualifying_key=True)
    subject_id = await create_subject_fixture("Theory of Computation", "toc-past")

    # 1. Create a conversation while entitled
    create_resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "Turing Machines"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201
    conv_id = create_resp.json()["id"]

    # 2. Deactivate the contributor's credential (e.g. disabled or deleted)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE public.api_key_metadata SET status = 'disabled' WHERE owner_id = $1",
            user_id,
        )

    # 3. Listing conversations must STILL succeed (historical access permitted)
    list_resp = await client.get(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.status_code == 200
    conv_titles = [c["title"] for c in list_resp.json()["items"]]
    assert "Turing Machines" in conv_titles

    # 4. Reading conversation metadata and message transcript must STILL succeed
    detail_resp = await client.get(
        f"/api/v1/chat/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["title"] == "Turing Machines"

    msg_resp = await client.get(
        f"/api/v1/chat/conversations/{conv_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert msg_resp.status_code == 200

    # 5. But creating a NEW conversation must fail!
    new_fail_resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "Decidability"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert new_fail_resp.status_code == 403
    assert new_fail_resp.json()["error"]["code"] == "CHAT_CONTRIBUTION_REQUIRED"


@pytest.mark.asyncio
async def test_non_contributor_can_access_knowledge_base(client: httpx.AsyncClient):
    """Approved student without credential contribution retains unrestricted Knowledge Base access."""
    _, non_contrib_token, _ = await create_user_fixture("kb_reader", with_qualifying_key=False)
    await create_subject_fixture("Software Engineering", "se-kb-test")

    resp = await client.get(
        "/api/v1/kb/subjects",
        headers={"Authorization": f"Bearer {non_contrib_token}"},
    )
    assert resp.status_code == 200
    subjects = resp.json()
    assert any(s["slug"] == "se-kb-test" for s in subjects)


@pytest.mark.asyncio
async def test_chat_entitlement_endpoint(client: httpx.AsyncClient):
    """GET /api/v1/me/chat-entitlement returns authoritative eligibility state."""
    # 1. Non-contributor
    _, non_contrib_token, _ = await create_user_fixture("entitle_none", with_qualifying_key=False)
    res_none = await client.get(
        "/api/v1/me/chat-entitlement",
        headers={"Authorization": f"Bearer {non_contrib_token}"},
    )
    assert res_none.status_code == 200
    data_none = res_none.json()
    assert data_none["eligible"] is False
    assert data_none["can_chat"] is False
    assert data_none["reason_code"] == "NO_CREDENTIAL_CONTRIBUTED"

    # 2. Contributor with valid Groq key
    _, contrib_token, _ = await create_user_fixture("entitle_valid", with_qualifying_key=True)
    res_valid = await client.get(
        "/api/v1/me/chat-entitlement",
        headers={"Authorization": f"Bearer {contrib_token}"},
    )
    assert res_valid.status_code == 200
    data_valid = res_valid.json()
    assert data_valid["eligible"] is True
    assert data_valid["can_chat"] is True
    assert data_valid["reason_code"] is None

    # 3. Admin account
    _, admin_token, _ = await create_user_fixture(
        "admin_entitle", role="admin", with_qualifying_key=False
    )
    res_admin = await client.get(
        "/api/v1/me/chat-entitlement",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["eligible"] is True


@pytest.mark.asyncio
async def test_local_ollama_and_unapproved_providers_do_not_grant_entitlement(
    client: httpx.AsyncClient,
):
    """Local Ollama runtime and unapproved cloud providers do NOT satisfy contribution entitlement."""
    user_id, token, _ = await create_user_fixture("ollama_user", with_qualifying_key=False)
    pool = get_pool()

    # Provision active & valid Ollama credential
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent
            )
            VALUES ($1, $2, 'ollama', 'Local Ollama', 'ollama_local', $3, 'active', 'valid', 'community_shared', true);
            """,
            uuid4(),
            user_id,
            uuid4().hex[:16],
        )

    res_ollama = await client.get(
        "/api/v1/me/chat-entitlement",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_ollama.status_code == 200
    assert res_ollama.json()["eligible"] is False
    assert res_ollama.json()["reason_code"] == "PROVIDER_NOT_ELIGIBLE_FOR_SHARING"

    # Provision active & valid Gemini credential (Gemini has allows_community_sharing = false)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.api_key_metadata (
                id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                status, validation_status, sharing_scope, shared_consent
            )
            VALUES ($1, $2, 'gemini', 'Private Gemini', 'AIza_test', $3, 'active', 'valid', 'community_shared', true);
            """,
            uuid4(),
            user_id,
            uuid4().hex[:16],
        )

    res_gemini = await client.get(
        "/api/v1/me/chat-entitlement",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_gemini.status_code == 200
    assert res_gemini.json()["eligible"] is False
    assert res_gemini.json()["reason_code"] == "PROVIDER_NOT_ELIGIBLE_FOR_SHARING"


# ==============================================================================
# 2. Conversation Lifecycle (CRUD) Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_create_conversation_success(client: httpx.AsyncClient):
    """Approved student can create a conversation associated with an active subject."""
    _, token, _ = await create_user_fixture("student")
    subject_id = await create_subject_fixture("Operating Systems", "os-crud")

    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "CPU Scheduling"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "CPU Scheduling"
    assert data["subject_id"] == str(subject_id)
    assert data["subject_name"] == "Operating Systems"
    assert data["status"] == "active"
    assert data["message_count"] == 0


@pytest.mark.asyncio
async def test_create_conversation_default_title(client: httpx.AsyncClient):
    """Omitting title defaults to 'New conversation — {subject.name}'."""
    _, token, _ = await create_user_fixture("student")
    subject_id = await create_subject_fixture("Database Systems", "dbms-crud")

    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "New conversation — Database Systems"


@pytest.mark.asyncio
async def test_create_conversation_inactive_subject_rejected(client: httpx.AsyncClient):
    """Creating a conversation in an inactive subject must return HTTP 400."""
    _, token, _ = await create_user_fixture("student")
    inactive_subj_id = await create_subject_fixture(
        "Archived Course", "archived-c", is_active=False
    )

    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(inactive_subj_id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "SUBJECT_INACTIVE"


@pytest.mark.asyncio
async def test_create_conversation_invalid_subject_rejected(client: httpx.AsyncClient):
    """Creating a conversation with a non-existent subject ID must return HTTP 404."""
    _, token, _ = await create_user_fixture("student")
    fake_subj_id = str(uuid4())

    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": fake_subj_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SUBJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_title_validation(client: httpx.AsyncClient):
    """Whitespace-only, oversized (>255), and control characters must be rejected."""
    _, token, _ = await create_user_fixture("student")
    subject_id = await create_subject_fixture("Compiler Design", "cd-test")

    # Whitespace only
    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "    "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422

    # Oversized > 255 chars
    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "A" * 256},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422

    # Control character
    resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "Bad\x00Title"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ==============================================================================
# 3. Ownership & Cross-User Isolation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_cross_user_isolation(client: httpx.AsyncClient):
    """Student B must not be able to view, rename, archive, or delete Student A's conversation."""
    _, token_a, _ = await create_user_fixture("student_a")
    _, token_b, _ = await create_user_fixture("student_b")
    subject_id = await create_subject_fixture("Computer Networks", "cn-iso")

    # Student A creates conversation
    create_resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "OSI Model Review"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    conv_id = create_resp.json()["id"]

    # Student B cannot retrieve Student A's conversation
    get_resp = await client.get(
        f"/api/v1/chat/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    # Student B cannot rename Student A's conversation
    patch_resp = await client.patch(
        f"/api/v1/chat/conversations/{conv_id}",
        json={"title": "Hacked Title"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert patch_resp.status_code == 404

    # Student B cannot archive Student A's conversation
    archive_resp = await client.post(
        f"/api/v1/chat/conversations/{conv_id}/archive",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert archive_resp.status_code == 404

    # Student B cannot delete Student A's conversation
    del_resp = await client.delete(
        f"/api/v1/chat/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert del_resp.status_code == 404

    # Student B listing conversations sees 0 items
    list_resp = await client.get(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total_count"] == 0
    assert len(list_resp.json()["items"]) == 0


# ==============================================================================
# 4. Lifecycle Mutations: Rename, Archive, Restore, Delete
# ==============================================================================


@pytest.mark.asyncio
async def test_conversation_mutations(client: httpx.AsyncClient):
    """Owner can rename, archive, restore, and delete their conversation."""
    _, token, _ = await create_user_fixture("student")
    subject_id = await create_subject_fixture("Algorithms", "algo-mut")

    create_resp = await client.post(
        "/api/v1/chat/conversations",
        json={"subject_id": str(subject_id), "title": "Dijkstra Algorithm"},
        headers={"Authorization": f"Bearer {token}"},
    )
    conv_id = create_resp.json()["id"]

    # 1. Rename
    rename_resp = await client.patch(
        f"/api/v1/chat/conversations/{conv_id}",
        json={"title": "Shortest Path & Dijkstra"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rename_resp.status_code == 200
    assert rename_resp.json()["title"] == "Shortest Path & Dijkstra"

    # 2. Archive
    archive_resp = await client.post(
        f"/api/v1/chat/conversations/{conv_id}/archive",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "archived"
    assert archive_resp.json()["archived_at"] is not None

    # Excluded from active list
    list_active = await client.get(
        "/api/v1/chat/conversations?status=active",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(list_active.json()["items"]) == 0

    # Present in archived list
    list_archived = await client.get(
        "/api/v1/chat/conversations?status=archived",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert len(list_archived.json()["items"]) == 1
    assert list_archived.json()["items"][0]["id"] == conv_id

    # 3. Restore
    restore_resp = await client.post(
        f"/api/v1/chat/conversations/{conv_id}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["status"] == "active"
    assert restore_resp.json()["archived_at"] is None

    # 4. Delete
    delete_resp = await client.delete(
        f"/api/v1/chat/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_resp.status_code == 204

    # Subsequent fetch returns 404
    get_after = await client.get(
        f"/api/v1/chat/conversations/{conv_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_after.status_code == 404


# ==============================================================================
# 5. Message History & Cursor Pagination Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_message_pagination(client: httpx.AsyncClient):
    """Validate message history retrieval, cursor pagination, and ordering."""
    user_id, token, profile = await create_user_fixture("student")
    subject_id = await create_subject_fixture("Software Engineering", "se-msg")

    claims = {"sub": str(user_id), "role": "authenticated"}

    # Create conversation
    conv = await ChatService.create_conversation(
        user_id=user_id, claims=claims, subject_id=subject_id, title="Agile Sprints"
    )

    # Empty history
    empty_resp = await client.get(
        f"/api/v1/chat/conversations/{conv.id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert empty_resp.status_code == 200
    assert empty_resp.json()["total_count"] == 0
    assert len(empty_resp.json()["items"]) == 0

    # Insert 5 test messages sequentially
    for i in range(1, 6):
        role = "user" if i % 2 == 1 else "assistant"
        await ChatService.create_message_internal(
            user_id=user_id,
            claims=claims,
            conversation_id=conv.id,
            role=role,
            content=f"Turn message {i}",
        )

    # Fetch with limit = 3 (returns newest 3 messages: 3, 4, 5)
    page1 = await client.get(
        f"/api/v1/chat/conversations/{conv.id}/messages?limit=3",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert page1.status_code == 200
    data1 = page1.json()
    assert data1["total_count"] == 5
    assert len(data1["items"]) == 3
    assert data1["has_more"] is True
    # Returned chronologically
    assert [m["sequence_number"] for m in data1["items"]] == [3, 4, 5]
    assert data1["next_cursor"] == "3"  # Cursor for fetching before sequence 3

    # Fetch page 2 using cursor
    page2 = await client.get(
        f"/api/v1/chat/conversations/{conv.id}/messages?limit=3&cursor={data1['next_cursor']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert page2.status_code == 200
    data2 = page2.json()
    assert len(data2["items"]) == 2
    assert [m["sequence_number"] for m in data2["items"]] == [1, 2]
    assert data2["has_more"] is False
    assert data2["next_cursor"] is None


# ==============================================================================
# 6. Concurrency Safety: Atomic Message Sequence Allocation
# ==============================================================================


@pytest.mark.asyncio
async def test_concurrent_message_insertion_sequence_integrity():
    """Concurrent message insertions must generate strictly monotonic, duplicate-free sequences."""
    user_id, _, _ = await create_user_fixture("student")
    subject_id = await create_subject_fixture("Distributed Systems", "dist-sys")

    claims = {"sub": str(user_id), "role": "authenticated"}
    conv = await ChatService.create_conversation(
        user_id=user_id, claims=claims, subject_id=subject_id, title="Consensus Protocols"
    )

    # Fire 10 simultaneous insertions concurrently
    async def _insert(idx: int):
        return await ChatService.create_message_internal(
            user_id=user_id,
            claims=claims,
            conversation_id=conv.id,
            role="user",
            content=f"Concurrent message {idx}",
        )

    results = await asyncio.gather(*[_insert(i) for i in range(10)])

    # Collect allocated sequence numbers
    seqs = [r.sequence_number for r in results]
    assert len(seqs) == 10
    # Assert sequence numbers are strictly 1 to 10 with no gaps and no duplicates
    assert sorted(seqs) == list(range(1, 11))
    assert len(set(seqs)) == 10

    # Verify conversation message_count is 10
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT message_count, next_sequence_number FROM public.chat_conversations WHERE id = $1",
            conv.id,
        )
        assert row["message_count"] == 10
        assert row["next_sequence_number"] == 11
