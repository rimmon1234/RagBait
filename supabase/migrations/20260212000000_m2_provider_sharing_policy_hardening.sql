-- ==============================================================================
-- Migration: Provider Sharing Policy Hardening
-- Restrict community sharing to providers with authoritative billing verification.
-- ==============================================================================

UPDATE public.llm_providers
SET allows_community_sharing = false
WHERE id IN ('groq', 'cerebras', 'gemini');
