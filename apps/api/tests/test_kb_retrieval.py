"""Unit and integration tests for M4.4 Hybrid Retrieval, RRF Ranking & Subject Scoping."""

import math
from uuid import UUID, uuid4

import pytest

from app.core.database import get_pool, init_db_pool
from app.core.errors import AuthorizationError, BadRequestError, NotFoundError
from app.modules.knowledge_base.embeddings import embed_passages
from app.modules.knowledge_base.retrieval import (
    RetrievalResult,
    RetrievalService,
    RetrievedChunk,
    RRFParameterError,
    compute_rrf_score,
    fuse_ranked_candidates,
    prepare_query_vector,
    validate_rrf_parameters,
)


@pytest.fixture(autouse=True)
async def ensure_db_pool():
    """Ensure database connection pool is initialized for async test cases."""
    await init_db_pool()
    yield


# ------------------------------------------------------------------------------
# 1. RRF Ranking & Parameter Validation Unit Tests
# ------------------------------------------------------------------------------


def test_rrf_parameter_validation():
    """Verify mathematical parameter boundaries for Reciprocal Rank Fusion."""
    # Valid parameters pass without error
    validate_rrf_parameters(k=60, vector_weight=0.60, fts_weight=0.40)
    validate_rrf_parameters(k=1, vector_weight=1.0, fts_weight=0.0)

    # Negative or zero k
    with pytest.raises(RRFParameterError, match="must be positive"):
        validate_rrf_parameters(k=0)
    with pytest.raises(RRFParameterError, match="must be positive"):
        validate_rrf_parameters(k=-10)

    # Non-finite weights
    with pytest.raises(RRFParameterError, match="must be finite"):
        validate_rrf_parameters(vector_weight=float("nan"))
    with pytest.raises(RRFParameterError, match="must be finite"):
        validate_rrf_parameters(fts_weight=float("inf"))

    # Negative weights
    with pytest.raises(RRFParameterError, match="must be non-negative"):
        validate_rrf_parameters(vector_weight=-0.1)
    with pytest.raises(RRFParameterError, match="must be non-negative"):
        validate_rrf_parameters(fts_weight=-0.5)

    # Zero total weight
    with pytest.raises(RRFParameterError, match="strictly positive"):
        validate_rrf_parameters(vector_weight=0.0, fts_weight=0.0)


def test_rrf_score_computation():
    """Verify exact formula calculation for vector and lexical ranking positions."""
    k = 60
    w_vec = 0.60
    w_fts = 0.40

    # 1. Present in both branches: rank 1 vector, rank 2 FTS
    score_both = compute_rrf_score(
        vector_rank=1, fts_rank=2, k=k, vector_weight=w_vec, fts_weight=w_fts
    )
    expected_both = (0.60 / (60 + 1)) + (0.40 / (60 + 2))
    assert abs(score_both - expected_both) < 1e-9

    # 2. Present only in vector branch
    score_vec_only = compute_rrf_score(
        vector_rank=1, fts_rank=None, k=k, vector_weight=w_vec, fts_weight=w_fts
    )
    assert abs(score_vec_only - (0.60 / 61)) < 1e-9

    # 3. Present only in FTS branch
    score_fts_only = compute_rrf_score(
        vector_rank=None, fts_rank=1, k=k, vector_weight=w_vec, fts_weight=w_fts
    )
    assert abs(score_fts_only - (0.40 / 61)) < 1e-9

    # 4. Missing from both branches
    assert compute_rrf_score(vector_rank=None, fts_rank=None) == 0.0

    # 5. Invalid non-positive rank numbers
    with pytest.raises(RRFParameterError, match="1-based positive integer"):
        compute_rrf_score(vector_rank=0, fts_rank=1)
    with pytest.raises(RRFParameterError, match="1-based positive integer"):
        compute_rrf_score(vector_rank=1, fts_rank=-1)


def test_fuse_ranked_candidates_deduplication_and_ordering():
    """Verify Python-side candidate fusion, deduplication, and deterministic tie-breaking."""
    c1 = uuid4()
    c2 = uuid4()
    c3 = uuid4()

    vector_candidates = [
        {"chunk_id": c1, "raw_text": "Chunk 1", "vector_rank": 1},
        {"chunk_id": c2, "raw_text": "Chunk 2", "vector_rank": 2},
    ]

    fts_candidates = [
        {"chunk_id": c2, "raw_text": "Chunk 2", "fts_rank": 1},
        {"chunk_id": c3, "raw_text": "Chunk 3", "fts_rank": 2},
    ]

    # Fuse with default weights (0.60 vector, 0.40 FTS, k=60)
    fused = fuse_ranked_candidates(vector_candidates, fts_candidates, k=60, limit=5)

    assert len(fused) == 3
    # c2 is present in both branches:
    # score(c2) = (0.60 / 62) + (0.40 / 61) = 0.009677 + 0.006557 = 0.016234
    # score(c1) = (0.60 / 61) = 0.009836
    # score(c3) = (0.40 / 62) = 0.006452
    # Ordering must be: c2 (rank 1) > c1 (rank 2) > c3 (rank 3)
    assert fused[0]["chunk_id"] == c2
    assert fused[0]["final_rank"] == 1
    assert fused[0]["vector_rank"] == 2
    assert fused[0]["fts_rank"] == 1

    assert fused[1]["chunk_id"] == c1
    assert fused[1]["final_rank"] == 2

    assert fused[2]["chunk_id"] == c3
    assert fused[2]["final_rank"] == 3

    # Limit parameter bounds output
    limited = fuse_ranked_candidates(vector_candidates, fts_candidates, limit=2)
    assert len(limited) == 2


# ------------------------------------------------------------------------------
# 2. Query Preparation Unit Tests
# ------------------------------------------------------------------------------


def test_prepare_query_vector_validation():
    """Verify query validation, token constraints, and real ONNX vector output."""
    # Empty query rejected
    with pytest.raises(BadRequestError, match="cannot be empty"):
        prepare_query_vector("")

    with pytest.raises(BadRequestError, match="cannot be empty"):
        prepare_query_vector("   \t\n  ")

    # Excessive query length rejected (> 128 tokens)
    long_query = "operating system concurrency and deadlock detection " * 35
    with pytest.raises(BadRequestError, match="exceeds maximum allowed limit"):
        prepare_query_vector(long_query)

    # Valid query generates 384-dimensional unit-norm vector
    res = prepare_query_vector("What is CPU scheduling in operating systems?")
    assert len(res.vector) == 384
    assert res.dimensions == 384
    assert res.model_fingerprint == "e5_small_int8_761b726d_f80102d3"
    norm = math.sqrt(sum(x * x for x in res.vector))
    assert abs(norm - 1.0) < 1e-4


# ------------------------------------------------------------------------------
# 3. Authorization & Security Unit Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_authorization_security_gates():
    """Verify authorization checks for user approval, conversation ownership, and subject status."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Setup users: approved vs pending vs suspended
            user_approved = uuid4()
            user_other = uuid4()
            user_pending = uuid4()
            user_suspended = uuid4()

            for uid, em, st in [
                (user_approved, f"appr_{uuid4().hex[:8]}@test.edu", "approved"),
                (user_other, f"other_{uuid4().hex[:8]}@test.edu", "approved"),
                (user_pending, f"pend_{uuid4().hex[:8]}@test.edu", "pending"),
                (user_suspended, f"susp_{uuid4().hex[:8]}@test.edu", "suspended"),
            ]:
                await conn.execute(
                    """
                    INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
                    VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{}'::jsonb);
                    """,
                    uid,
                    em,
                )
                await conn.execute(
                    """
                    UPDATE public.profiles
                    SET approval_status = $2
                    WHERE id = $1;
                    """,
                    uid,
                    st,
                )

            # Setup subjects: active vs inactive
            sub_active = uuid4()
            sub_inactive = uuid4()
            sub_a_suffix = sub_active.hex[:6]
            sub_i_suffix = sub_inactive.hex[:6]
            await conn.execute(
                """
                INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
                VALUES
                  ($1, $3, $4, 'Active CS Subject', true),
                  ($2, $5, $6, 'Inactive CS Subject', false);
                """,
                sub_active,
                sub_inactive,
                f"Active CS {sub_a_suffix}",
                f"active-cs-{sub_a_suffix}",
                f"Inactive CS {sub_i_suffix}",
                f"inactive-cs-{sub_i_suffix}",
            )

            # Setup conversations
            conv_valid = uuid4()
            conv_archived = uuid4()
            conv_inactive_sub = uuid4()
            conv_other_user = uuid4()

            await conn.execute(
                """
                INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status)
                VALUES
                  ($1, $2, $3, 'Valid Conversation', 'active'),
                  ($4, $2, $3, 'Archived Conversation', 'archived'),
                  ($5, $2, $6, 'Inactive Subject Conversation', 'active'),
                  ($7, $8, $3, 'Other User Conversation', 'active');
                """,
                conv_valid,
                user_approved,
                sub_active,
                conv_archived,
                conv_inactive_sub,
                sub_inactive,
                conv_other_user,
                user_other,
            )

            from app.modules.knowledge_base.retrieval.authorization import (
                verify_retrieval_authorization,
            )

            # Case 1: Approved user accessing owned active conversation succeeds
            resolved_sub = await verify_retrieval_authorization(
                conn, user_id=user_approved, conversation_id=conv_valid
            )
            assert resolved_sub == sub_active

            # Case 2: Unapproved (pending) user rejected
            with pytest.raises(AuthorizationError, match="not authorized"):
                await verify_retrieval_authorization(
                    conn, user_id=user_pending, conversation_id=conv_valid
                )

            # Case 3: Suspended user rejected
            with pytest.raises(AuthorizationError, match="not authorized"):
                await verify_retrieval_authorization(
                    conn, user_id=user_suspended, conversation_id=conv_valid
                )

            # Case 4: Cross-user access denied (User A accessing User B's conversation)
            with pytest.raises(AuthorizationError, match="do not own this conversation"):
                await verify_retrieval_authorization(
                    conn, user_id=user_approved, conversation_id=conv_other_user
                )

            # Case 5: Archived conversation rejected
            with pytest.raises(AuthorizationError, match="archived conversation"):
                await verify_retrieval_authorization(
                    conn, user_id=user_approved, conversation_id=conv_archived
                )

            # Case 6: Conversation with inactive subject rejected
            with pytest.raises(
                AuthorizationError, match="subject associated with this conversation is inactive"
            ):
                await verify_retrieval_authorization(
                    conn, user_id=user_approved, conversation_id=conv_inactive_sub
                )

            # Case 7: Non-existent conversation raises NotFoundError
            with pytest.raises(NotFoundError, match="Conversation not found"):
                await verify_retrieval_authorization(
                    conn, user_id=user_approved, conversation_id=uuid4()
                )


# ------------------------------------------------------------------------------
# 4. End-to-End Real-Model Multilingual Integration Test
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_service_end_to_end_multilingual():
    """Verify complete retrieval flow with real FastEmbed ONNX inference and PostgreSQL match function."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Setup approved student user
            student_id = uuid4()
            student_email = f"student_{uuid4().hex[:8]}@test.edu"
            await conn.execute(
                """
                INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
                VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{}'::jsonb);
                """,
                student_id,
                student_email,
            )
            await conn.execute(
                """
                UPDATE public.profiles
                SET approval_status = 'approved'
                WHERE id = $1;
                """,
                student_id,
            )

            # Setup subjects: Subject A (active) and Subject B (different subject)
            subject_a = uuid4()
            subject_b = uuid4()
            sa_suffix = subject_a.hex[:6]
            sb_suffix = subject_b.hex[:6]
            await conn.execute(
                """
                INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
                VALUES
                  ($1, $3, $4, 'Subject A', true),
                  ($2, $5, $6, 'Subject B', true);
                """,
                subject_a,
                subject_b,
                f"Computer Architecture {sa_suffix}",
                f"comp-arch-{sa_suffix}",
                f"Cell Biology {sb_suffix}",
                f"cell-bio-{sb_suffix}",
            )

            # Setup topics
            topic_a = uuid4()
            topic_b = uuid4()
            ta_suffix = topic_a.hex[:6]
            tb_suffix = topic_b.hex[:6]
            await conn.execute(
                """
                INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active)
                VALUES
                  ($1, $2, $5, $6, true),
                  ($3, $4, $7, $8, true);
                """,
                topic_a,
                subject_a,
                topic_b,
                subject_b,
                f"Operating Systems Topic {ta_suffix}",
                f"os-topic-{ta_suffix}",
                f"Genetics Topic {tb_suffix}",
                f"genetics-topic-{tb_suffix}",
            )

            # Setup active document in Subject A
            doc_a = uuid4()
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                  id, subject_id, topic_id, title, original_filename,
                  storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                  indexing_status, active_chunk_version, embedding_status, active_embedding_fingerprint
                )
                VALUES (
                  $1, $2, $3, 'OS Memory and Scheduling Notes', 'os_notes.pdf',
                  $4, 'application/pdf', 2048,
                  'uploaded', 'completed', 'indexed', 1, 'embedded', 'e5_small_int8_761b726d_f80102d3'
                );
                """,
                doc_a,
                subject_a,
                topic_a,
                f"documents/{doc_a}/source.pdf",
            )

            # Setup document in Subject B (cross-subject isolation check)
            doc_b = uuid4()
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                  id, subject_id, topic_id, title, original_filename,
                  storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                  indexing_status, active_chunk_version, embedding_status, active_embedding_fingerprint
                )
                VALUES (
                  $1, $2, $3, 'Cellular Respiration in Mitochondria', 'bio_notes.pdf',
                  $4, 'application/pdf', 2048,
                  'uploaded', 'completed', 'indexed', 1, 'embedded', 'e5_small_int8_761b726d_f80102d3'
                );
                """,
                doc_b,
                subject_b,
                topic_b,
                f"documents/{doc_b}/source.pdf",
            )

            # Raw passages across English, Bengali, and Hindi
            raw_passages_a = [
                # Chunk 0: English
                "Operating systems coordinate CPU scheduling, memory management, and concurrent processes across hardware threads.",
                # Chunk 1: Bengali
                "কম্পিউটার বিজ্ঞান তথ্য এবং গণনার তাত্ত্বিক ভিত্তি নিয়ে আলোচনা করে।",
                # Chunk 2: Hindi
                "कंप्यूटर विज्ञान सूचना और संगणना के सैद्धांतिक आधारों का अध्ययन है।",
            ]
            raw_passage_b = [
                "Mitochondria generate adenosine triphosphate through cellular respiration and oxidative phosphorylation."
            ]

            # Insert chunks for Doc A
            chunk_ids_a: list[UUID] = []
            for idx, text in enumerate(raw_passages_a):
                cid = uuid4()
                chunk_ids_a.append(cid)
                await conn.execute(
                    """
                    INSERT INTO public.kb_document_chunks (
                      id, document_id, chunking_version, chunk_index, page_number_start, page_number_end,
                      raw_text, character_count, token_count, page_spans, content_hash, is_active
                    )
                    VALUES ($1, $2, 1, $3, $4, $4, $5, length($5), 15, $6::jsonb, $7, true);
                    """,
                    cid,
                    doc_a,
                    idx,
                    idx + 1,
                    text,
                    f'[{{"page_number": {idx + 1}, "start_char": 0, "end_char": {len(text)}}}]',
                    f"hash_mock_{idx}_{cid}",
                )

            # Insert chunk for Doc B
            chunk_id_b = uuid4()
            await conn.execute(
                """
                INSERT INTO public.kb_document_chunks (
                  id, document_id, chunking_version, chunk_index, page_number_start, page_number_end,
                  raw_text, character_count, token_count, page_spans, content_hash, is_active
                )
                VALUES ($1, $2, 1, 0, 1, 1, $3, length($3), 15, '[{"page_number": 1, "start_char": 0, "end_char": 100}]'::jsonb, 'hash_bio_b', true);
                """,
                chunk_id_b,
                doc_b,
                raw_passage_b[0],
            )

            # Generate real FastEmbed INT8 ONNX embeddings
            embeddings_a = embed_passages(raw_passages_a)
            embedding_b = embed_passages(raw_passage_b)

            # Insert real vectors into public.kb_chunk_embeddings
            for cid, emb_res, text in zip(chunk_ids_a, embeddings_a, raw_passages_a, strict=False):
                vec_str = f"[{','.join(f'{x:.8f}' for x in emb_res.vector)}]"
                await conn.execute(
                    """
                    INSERT INTO public.kb_chunk_embeddings (
                      chunk_id, document_id, chunk_version, model_id, model_fingerprint,
                      dimensions, embedding, source_content_hash
                    )
                    VALUES ($1, $2, 1, 'intfloat/multilingual-e5-small', $3, 384, $4::text::extensions.vector, $5);
                    """,
                    cid,
                    doc_a,
                    emb_res.model_fingerprint,
                    vec_str,
                    f"hash_mock_{chunk_ids_a.index(cid)}_{cid}",
                )

            vec_str_b = f"[{','.join(f'{x:.8f}' for x in embedding_b[0].vector)}]"
            await conn.execute(
                """
                INSERT INTO public.kb_chunk_embeddings (
                  chunk_id, document_id, chunk_version, model_id, model_fingerprint,
                  dimensions, embedding, source_content_hash
                )
                VALUES ($1, $2, 1, 'intfloat/multilingual-e5-small', $3, 384, $4::text::extensions.vector, 'hash_bio_b');
                """,
                chunk_id_b,
                doc_b,
                embedding_b[0].model_fingerprint,
                vec_str_b,
            )

            # Create student conversation bound to Subject A
            conv_id = uuid4()
            await conn.execute(
                """
                INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status)
                VALUES ($1, $2, $3, 'Student Study Chat', 'active');
                """,
                conv_id,
                student_id,
                subject_a,
            )

            # Test 1: English Retrieval Execution
            res_en = await RetrievalService.retrieve_subject_chunks(
                user_id=student_id,
                conversation_id=conv_id,
                query_text="What is CPU scheduling in operating systems?",
                limit=3,
                conn=conn,
            )
            assert isinstance(res_en, RetrievalResult)
            assert res_en.subject_id == subject_a
            assert len(res_en.chunks) >= 1
            # Verify top result is English chunk (chunk 0)
            top_en = res_en.chunks[0]
            assert isinstance(top_en, RetrievedChunk)
            assert top_en.chunk_id == chunk_ids_a[0]
            assert top_en.page_number_start == 1
            assert top_en.page_number_end == 1
            assert top_en.vector_rank is not None
            assert top_en.fts_rank is not None
            assert top_en.rrf_score > 0.0
            assert top_en.final_rank == 1

            # Test 2: Bengali Retrieval Execution
            res_bn = await RetrievalService.retrieve_subject_chunks(
                user_id=student_id,
                conversation_id=conv_id,
                query_text="কম্পিউটার বিজ্ঞান তথ্য ভিত্তি",
                limit=3,
                conn=conn,
            )
            assert len(res_bn.chunks) >= 1
            # Verify top result is Bengali chunk (chunk 1)
            top_bn = res_bn.chunks[0]
            assert top_bn.chunk_id == chunk_ids_a[1]
            assert top_bn.page_number_start == 2
            assert top_bn.fts_rank is not None

            # Test 3: Hindi Retrieval Execution
            res_hi = await RetrievalService.retrieve_subject_chunks(
                user_id=student_id,
                conversation_id=conv_id,
                query_text="कंप्यूटर विज्ञान सूचना",
                limit=3,
                conn=conn,
            )
            assert len(res_hi.chunks) >= 1
            # Verify top result is Hindi chunk (chunk 2)
            top_hi = res_hi.chunks[0]
            assert top_hi.chunk_id == chunk_ids_a[2]
            assert top_hi.page_number_start == 3
            assert top_hi.fts_rank is not None

            # Test 4: Cross-Subject Isolation Guarantee
            # Biological science chunk from Subject B must NEVER appear in Subject A conversation
            all_retrieved_ids = {c.chunk_id for c in res_en.chunks + res_bn.chunks + res_hi.chunks}
            assert chunk_id_b not in all_retrieved_ids

            # Test 5: Document Archival Fencing
            await conn.execute(
                """
                UPDATE public.kb_documents
                SET is_archived = true
                WHERE id = $1;
                """,
                doc_a,
            )
            res_after_archive = await RetrievalService.retrieve_subject_chunks(
                user_id=student_id,
                conversation_id=conv_id,
                query_text="CPU scheduling",
                limit=3,
                conn=conn,
            )
            assert len(res_after_archive.chunks) == 0


# ------------------------------------------------------------------------------
# 5. Query Plan Inspection (EXPLAIN ANALYZE)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_explain_query_plan():
    """Verify that PostgreSQL executes match_subject_chunks() cleanly with expected query plan."""
    pool = get_pool()
    async with pool.acquire() as conn:
        dummy_sub = uuid4()
        dummy_vec = f"[{','.join(f'{x:.6f}' for x in [0.051] * 384)}]"

        plan_rows = await conn.fetch(
            f"""
            EXPLAIN (ANALYZE, BUFFERS)
            SELECT * FROM public.match_subject_chunks(
                '{dummy_sub}'::uuid,
                '{dummy_vec}'::text::extensions.vector,
                'Operating systems scheduling'
            );
            """
        )
        assert len(plan_rows) > 0
        plan_text = "\n".join(r["QUERY PLAN"] for r in plan_rows)
        # Confirms successful plan compilation and execution
        assert "Function Scan on match_subject_chunks" in plan_text


# ------------------------------------------------------------------------------
# 6. Evaluation Benchmark Suite (Recall@5, MRR, Precision@5)
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_evaluation_benchmark_suite():
    """Execute end-to-end evaluation suite across multilingual, keyword, formula, and acronym queries.

    Measures and compares:
    - Hybrid RRF (default: w_vec=0.6, w_fts=0.4, k=60)
    - Vector-only (w_vec=1.0, w_fts=0.0)
    - FTS-only (w_vec=0.0, w_fts=1.0)
    Across:
    - Recall@5
    - MRR (Mean Reciprocal Rank)
    - Precision@5
    - Cross-subject isolation guarantee (0% leakage)
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Setup student user
            student_id = uuid4()
            student_email = f"eval_{uuid4().hex[:8]}@test.edu"
            await conn.execute(
                """
                INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
                VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{}'::jsonb);
                """,
                student_id,
                student_email,
            )
            await conn.execute(
                """
                UPDATE public.profiles
                SET approval_status = 'approved'
                WHERE id = $1;
                """,
                student_id,
            )

            # Setup evaluation subject and distractor subject
            sub_eval = uuid4()
            sub_dist = uuid4()
            se_sfx = sub_eval.hex[:6]
            sd_sfx = sub_dist.hex[:6]
            await conn.execute(
                """
                INSERT INTO public.kb_subjects (id, name, slug, description, is_active)
                VALUES
                  ($1, $3, $4, 'Evaluation Subject', true),
                  ($2, $5, $6, 'Distractor Biology Subject', true);
                """,
                sub_eval,
                sub_dist,
                f"Evaluation Subject {se_sfx}",
                f"eval-subject-{se_sfx}",
                f"Distractor Subject {sd_sfx}",
                f"dist-subject-{sd_sfx}",
            )

            topic_eval = uuid4()
            topic_dist = uuid4()
            await conn.execute(
                """
                INSERT INTO public.kb_topics (id, subject_id, name, slug, is_active)
                VALUES
                  ($1, $2, $5, $6, true),
                  ($3, $4, $7, $8, true);
                """,
                topic_eval,
                sub_eval,
                topic_dist,
                sub_dist,
                f"Eval CS Topic {se_sfx}",
                f"eval-cs-{se_sfx}",
                f"Dist Bio Topic {sd_sfx}",
                f"dist-bio-{sd_sfx}",
            )

            doc_eval = uuid4()
            doc_dist = uuid4()
            await conn.execute(
                """
                INSERT INTO public.kb_documents (
                  id, subject_id, topic_id, title, original_filename,
                  storage_path, mime_type, file_size_bytes, upload_status, extraction_status,
                  indexing_status, active_chunk_version, embedding_status, active_embedding_fingerprint
                )
                VALUES
                  ($1, $2, $3, 'CS Core Comprehensive Handbook', 'cs_core.pdf',
                   $4, 'application/pdf', 4096, 'uploaded', 'completed', 'indexed', 1, 'embedded', 'e5_small_int8_761b726d_f80102d3'),
                  ($5, $6, $7, 'Cell Biology and Plant Physiology', 'bio_core.pdf',
                   $8, 'application/pdf', 4096, 'uploaded', 'completed', 'indexed', 1, 'embedded', 'e5_small_int8_761b726d_f80102d3');
                """,
                doc_eval,
                sub_eval,
                topic_eval,
                f"documents/{doc_eval}/source.pdf",
                doc_dist,
                sub_dist,
                topic_dist,
                f"documents/{doc_dist}/source.pdf",
            )

            # 10 Ground-truth passages in evaluation subject
            eval_passages = [
                # 0: English Concept Paraphrase (Virtual memory & paging)
                "Virtual memory gives processes the illusion of contiguous dedicated address space using paging and page tables translation.",
                # 1: English Exact Keyword (Deadlock conditions)
                "Deadlock occurs when four conditions hold simultaneously: mutual exclusion, hold and wait, no preemption, and circular wait.",
                # 2: English Acronym (TCP/IP network protocols)
                "The TCP/IP protocol suite establishes end-to-end communication with packet routing and error checking across networks.",
                # 3: English Formula (Amdahl's speedup law)
                "Amdahl's law calculates theoretical speedup: S = 1 / ((1 - p) + (p / s)) where p represents parallel fraction.",
                # 4: Bengali Concept Paraphrase (Memory management & virtual memory)
                "স্মৃতি ব্যবস্থাপন প্রক্রিয়া কম্পিউটার মেমোরি ব্যবহারের ভারসাম্য রক্ষা করে এবং ভার্চুয়াল মেমোরি সরবরাহ করে।",
                # 5: Bengali Exact Keyword (Computer science foundations)
                "কম্পিউটার বিজ্ঞান তথ্য এবং গণনার তাত্ত্বিক ভিত্তি নিয়ে বিস্তৃত গবেষণা করে।",
                # 6: Hindi Concept Paraphrase (Computer architecture & control flow)
                "कंप्यूटर आर्किटेक्चर हार्डवेयर और सॉफ्टवेयर घटकों के बीच इंटरफेस और नियंत्रण प्रवाह को परिभाषित करता है।",
                # 7: Hindi Exact Keyword (Data processing fundamentals)
                "संगणक विज्ञान सूचना और डेटा प्रोसेसिंग के मूलभूत सिद्धांतों का व्यवस्थित अध्ययन है।",
                # 8: Mixed Language - Bengali + English Acronyms
                "ক্লাউড সিস্টেমে API গেটওয়ে মাইক্রোসার্ভিস আর্কিটেকচারের জন্য লোড ব্যালেন্সিং এবং রাউটিং পরিচালনা করে।",
                # 9: Mixed Language - Hindi + English Acronyms
                "वेब आर्किटेक्चर में HTTP और REST प्रोटोकॉल वितरित क्लाइंट-सर्वर संचार को सक्षम बनाते हैं।",
            ]

            # 2 Distractor passages in different subject
            distractor_passages = [
                # 10: Biology distractor 1
                "Photosynthesis converts light energy into chemical energy within plant chloroplasts using chlorophyll pigments.",
                # 11: Biology distractor 2
                "DNA replication proceeds bi-directionally from origin replication forks using DNA polymerase enzymes.",
            ]

            # Insert evaluation chunks
            eval_chunk_ids: list[UUID] = []
            chunk_hash_map: dict[UUID, str] = {}
            for idx, text in enumerate(eval_passages):
                cid = uuid4()
                eval_chunk_ids.append(cid)
                chash = f"content_hash_eval_{idx}_{cid}"
                chunk_hash_map[cid] = chash
                await conn.execute(
                    """
                    INSERT INTO public.kb_document_chunks (
                      id, document_id, chunking_version, chunk_index, page_number_start, page_number_end,
                      raw_text, character_count, token_count, page_spans, content_hash, is_active
                    )
                    VALUES ($1, $2, 1, $3, $4, $4, $5, length($5), 18, $6::jsonb, $7, true);
                    """,
                    cid,
                    doc_eval,
                    idx,
                    idx + 1,
                    text,
                    f'[{{"page_number": {idx + 1}, "start_char": 0, "end_char": {len(text)}}}]',
                    chash,
                )

            # Insert distractor chunks
            dist_chunk_ids: list[UUID] = []
            for idx, text in enumerate(distractor_passages):
                cid = uuid4()
                dist_chunk_ids.append(cid)
                chash = f"content_hash_dist_{idx}_{cid}"
                chunk_hash_map[cid] = chash
                await conn.execute(
                    """
                    INSERT INTO public.kb_document_chunks (
                      id, document_id, chunking_version, chunk_index, page_number_start, page_number_end,
                      raw_text, character_count, token_count, page_spans, content_hash, is_active
                    )
                    VALUES ($1, $2, 1, $3, $4, $4, $5, length($5), 18, $6::jsonb, $7, true);
                    """,
                    cid,
                    doc_dist,
                    idx,
                    idx + 1,
                    text,
                    f'[{{"page_number": {idx + 1}, "start_char": 0, "end_char": {len(text)}}}]',
                    chash,
                )

            # Generate and insert real FastEmbed vectors for all chunks
            all_passages = eval_passages + distractor_passages
            all_chunk_ids = eval_chunk_ids + dist_chunk_ids
            all_doc_ids = [doc_eval] * len(eval_passages) + [doc_dist] * len(distractor_passages)
            all_embeddings = embed_passages(all_passages)

            for cid, did, emb_res in zip(all_chunk_ids, all_doc_ids, all_embeddings, strict=False):
                vec_str = f"[{','.join(f'{x:.8f}' for x in emb_res.vector)}]"
                await conn.execute(
                    """
                    INSERT INTO public.kb_chunk_embeddings (
                      chunk_id, document_id, chunk_version, model_id, model_fingerprint,
                      dimensions, embedding, source_content_hash
                    )
                    VALUES ($1, $2, 1, 'intfloat/multilingual-e5-small', $3, 384, $4::text::extensions.vector, $5);
                    """,
                    cid,
                    did,
                    emb_res.model_fingerprint,
                    vec_str,
                    chunk_hash_map[cid],
                )

            # Create conversations for evaluation subject and distractor subject
            conv_eval = uuid4()
            conv_dist = uuid4()
            await conn.execute(
                """
                INSERT INTO public.chat_conversations (id, user_id, subject_id, title, status)
                VALUES
                  ($1, $2, $3, 'Evaluation Chat', 'active'),
                  ($4, $5, $6, 'Distractor Chat', 'active');
                """,
                conv_eval,
                student_id,
                sub_eval,
                conv_dist,
                student_id,
                sub_dist,
            )

            # Test benchmark queries: query_text, expected_target_index
            benchmark_queries = [
                # 1. English Concept Paraphrase
                ("How does operating system provide an illusion of continuous address space?", 0),
                # 2. English Exact Keyword
                ("mutual exclusion and circular wait deadlock conditions", 1),
                # 3. English Acronym
                ("TCP/IP protocol suite packet routing", 2),
                # 4. English Formula
                ("Amdahl's law theoretical speedup formula", 3),
                # 5. Bengali Concept Paraphrase
                ("ভার্চুয়াল মেমোরি এবং মেমোরি নিয়ন্ত্রণ প্রক্রিয়া", 4),
                # 6. Bengali Exact Keyword
                ("কম্পিউটার বিজ্ঞান তথ্য এবং গণনার তাত্ত্বিক ভিত্তি", 5),
                # 7. Hindi Concept Paraphrase
                ("हार्डवेयर और सॉफ्टवेयर इंटरफेस नियंत्रण प्रवाह", 6),
                # 8. Hindi Exact Keyword
                ("संगणक विज्ञान सूचना और डेटा प्रोसेसिंग", 7),
                # 9. Mixed Language (Bengali + English Acronym)
                ("API গেটওয়ে এবং মাইক্রোসার্ভিস রাউটিং", 8),
                # 10. Mixed Language (Hindi + English Acronym)
                ("HTTP और REST प्रोटोकॉल वितरित संचार", 9),
            ]

            modes = [
                ("Hybrid RRF (0.6 / 0.4)", 0.60, 0.40),
                ("Vector Only (1.0 / 0.0)", 1.0, 0.0),
                ("FTS Only (0.0 / 1.0)", 0.0, 1.0),
            ]

            benchmark_results = {}

            for mode_name, w_vec, w_fts in modes:
                hits_at_5 = 0
                reciprocal_ranks = []
                precision_at_5_list = []
                cross_subject_leaks = 0

                for q_text, target_idx in benchmark_queries:
                    target_chunk_id = eval_chunk_ids[target_idx]

                    res = await RetrievalService.retrieve_subject_chunks(
                        user_id=student_id,
                        conversation_id=conv_eval,
                        query_text=q_text,
                        limit=5,
                        vector_weight=w_vec,
                        fts_weight=w_fts,
                        conn=conn,
                    )

                    retrieved_ids = [c.chunk_id for c in res.chunks]

                    # Check for cross-subject leakage
                    for dist_id in dist_chunk_ids:
                        if dist_id in retrieved_ids:
                            cross_subject_leaks += 1

                    # Metric: Recall@5 and Reciprocal Rank
                    if target_chunk_id in retrieved_ids:
                        hits_at_5 += 1
                        rank = retrieved_ids.index(target_chunk_id) + 1
                        reciprocal_ranks.append(1.0 / rank)
                        precision_at_5_list.append(1.0 / len(retrieved_ids))
                    else:
                        reciprocal_ranks.append(0.0)
                        precision_at_5_list.append(0.0)

                total_q = len(benchmark_queries)
                recall_at_5 = hits_at_5 / total_q
                mrr = sum(reciprocal_ranks) / total_q
                prec_at_5 = sum(precision_at_5_list) / total_q

                benchmark_results[mode_name] = {
                    "recall@5": recall_at_5,
                    "mrr": mrr,
                    "precision@5": prec_at_5,
                    "cross_subject_leaks": cross_subject_leaks,
                }

            # Print benchmark summary for audit log
            print("\n=== M4.4 RETRIEVAL BENCHMARK EVALUATION RESULTS ===")
            for mode, m in benchmark_results.items():
                print(
                    f"{mode:25} | Recall@5: {m['recall@5']:.4f} | MRR: {m['mrr']:.4f} | "
                    f"P@5: {m['precision@5']:.4f} | Cross-Subject Leaks: {m['cross_subject_leaks']}"
                )

            # Assert strict quality gates
            hybrid_m = benchmark_results["Hybrid RRF (0.6 / 0.4)"]
            assert hybrid_m["recall@5"] >= 0.90, (
                f"Hybrid Recall@5 {hybrid_m['recall@5']} below 0.90"
            )
            assert hybrid_m["mrr"] >= 0.80, f"Hybrid MRR {hybrid_m['mrr']} below 0.80"
            assert hybrid_m["cross_subject_leaks"] == 0, "Subject isolation leak detected"

            # Assert Distractor query behavior:
            # Query in evaluation chat about distractor topic returns 0 distractor chunks
            distractor_query = "chlorophyll pigments photosynthesis light chemical energy"
            res_dist_in_eval = await RetrievalService.retrieve_subject_chunks(
                user_id=student_id,
                conversation_id=conv_eval,
                query_text=distractor_query,
                limit=5,
                conn=conn,
            )
            for chunk in res_dist_in_eval.chunks:
                assert chunk.chunk_id not in dist_chunk_ids, "Subject boundary violation!"

            # Query in distractor chat returns distractor chunk as top result
            res_dist_in_dist = await RetrievalService.retrieve_subject_chunks(
                user_id=student_id,
                conversation_id=conv_dist,
                query_text=distractor_query,
                limit=5,
                conn=conn,
            )
            assert len(res_dist_in_dist.chunks) >= 1
            assert res_dist_in_dist.chunks[0].chunk_id == dist_chunk_ids[0]
