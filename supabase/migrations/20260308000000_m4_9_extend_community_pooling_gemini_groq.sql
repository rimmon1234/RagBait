-- ==============================================================================
-- Migration: Extend Community Pooling to Free-Tier Capable Gemini & Groq
-- ==============================================================================
-- Enable community key pooling across free-tier capable providers
-- ('openrouter', 'gemini', 'groq') so contributors pool spare free-tier keys
-- and route around per-key rate limits/availability, while keeping paid-only
-- providers ('openai', 'xai') and unverified/local runtimes strictly excluded.

UPDATE public.llm_providers
SET allows_community_sharing = true
WHERE id IN ('openrouter', 'gemini', 'groq');

UPDATE public.llm_providers
SET allows_community_sharing = false
WHERE id IN ('openai', 'xai', 'cerebras', 'ollama');
