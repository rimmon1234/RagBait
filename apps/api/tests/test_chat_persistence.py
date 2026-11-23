"""Comprehensive test suite for Phase 4 M4.1 Chat Persistence & Conversation Management."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import init_redis
from app.core.security import AuthenticatedUser
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


async def create_user_fixture(
    email_prefix: str,
    role: str = "user",
    approval_status: str = "approved",
) -> tuple[UUID, str, UserProfile]:
    """Create a verified test user in auth.users and public.profiles."""
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
    inactive_subj_id = await create_subject_fixture("Archived Course", "archived-c", is_active=False)

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
