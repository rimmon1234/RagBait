-- ==============================================================================
-- RagBait Phase 4 — Milestone M4.6: Generation Reliability & Load Control
-- Migration: 20260307000000_m4_6_generation_reliability.sql
-- Description:
--   Adds lightweight partial indexes on public.chat_messages to support fast
--   cross-process cancellation lookups and active generation checks.
-- ==============================================================================

CREATE INDEX IF NOT EXISTS idx_chat_messages_gen_req_id
    ON public.chat_messages (generation_request_id)
    WHERE generation_request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chat_messages_active_conv
    ON public.chat_messages (conversation_id, status)
    WHERE status IN ('pending', 'streaming');
