"""Authorization and security boundary verification for knowledge retrieval."""

import logging
from uuid import UUID

import asyncpg

from app.core.errors import AuthorizationError, NotFoundError

logger = logging.getLogger("ragbait.kb.retrieval.auth")


async def verify_retrieval_authorization(
    conn: asyncpg.Connection,
    user_id: UUID,
    conversation_id: UUID,
) -> UUID:
    """Verify that the caller owns the conversation and has approved access to its subject.

    Security Guarantees:
    1. Authenticated user profile exists and status is 'approved' (rejects 'pending', 'suspended', 'rejected').
    2. Conversation exists and belongs to the authenticated user (prevents cross-user access).
    3. Conversation is active (not archived).
    4. Associated academic subject exists and is active (prevents retrieval from inactive curricula).
    5. Returns the authoritative subject_id derived from the database conversation record.
       (Never trusts a client-supplied subject ID).

    Raises:
        AuthorizationError: If account is unapproved, conversation not owned, or subject inactive.
        NotFoundError: If conversation does not exist.
    """
    # 1. Verify user profile and approval status
    user_row = await conn.fetchrow(
        """
        SELECT id, approval_status
        FROM public.profiles
        WHERE id = $1
        """,
        user_id,
    )
    if not user_row:
        logger.warning(f"Retrieval rejected: user profile {user_id} not found.")
        raise AuthorizationError("User profile not found or inactive.")

    if user_row["approval_status"] != "approved":
        logger.warning(
            f"Retrieval rejected: user {user_id} account approval_status is '{user_row['approval_status']}', expected 'approved'."
        )
        raise AuthorizationError(
            f"Account status '{user_row['approval_status']}' is not authorized for knowledge retrieval."
        )

    # 2. Verify conversation existence and ownership
    conv_row = await conn.fetchrow(
        """
        SELECT id, user_id, subject_id, status
        FROM public.chat_conversations
        WHERE id = $1
        """,
        conversation_id,
    )
    if not conv_row:
        logger.warning(f"Retrieval rejected: conversation {conversation_id} not found.")
        raise NotFoundError("Conversation not found.")

    if conv_row["user_id"] != user_id:
        logger.warning(
            f"Security violation: user {user_id} attempted access to conversation {conversation_id} owned by {conv_row['user_id']}."
        )
        raise AuthorizationError("Access denied: you do not own this conversation.")

    if conv_row["status"] != "active":
        logger.warning(
            f"Retrieval rejected: conversation {conversation_id} has status '{conv_row['status']}'."
        )
        raise AuthorizationError(
            f"Cannot perform retrieval on a {conv_row['status']} conversation."
        )

    subject_id: UUID = conv_row["subject_id"]

    # 3. Verify academic subject is active
    subj_row = await conn.fetchrow(
        """
        SELECT id, is_active
        FROM public.kb_subjects
        WHERE id = $1
        """,
        subject_id,
    )
    if not subj_row or not subj_row["is_active"]:
        logger.warning(
            f"Retrieval rejected: subject {subject_id} for conversation {conversation_id} is inactive or deleted."
        )
        raise AuthorizationError(
            "The academic subject associated with this conversation is inactive."
        )

    return subject_id
