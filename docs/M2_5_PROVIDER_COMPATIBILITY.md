# RagBait M2.5 — Provider Compatibility & Upstream Inference Specifications

**Document Version**: 1.0.0  
**Last Verified Against Official Documentation**: 2026-02-07 (Simulated Environment Date: 2026-11-19)  
**Target Providers**: Google Gemini, Groq, OpenRouter, Cerebras  

---

## 1. Executive Overview

RagBait M2.5 delivers native, production-quality adapters for four initial cloud inference providers. The design enforces:
1. **Zero Raw Upstream Exposure**: Neither API keys, private headers, raw JSON chunks, nor provider error dumps are exposed to clients or standard log streams.
2. **Normalized Protocol Contract**: Every provider adapter maps incoming unified chat requests into provider-specific REST/SSE calls, and transforms upstream chunks into a uniform Server-Sent Events (SSE) streaming format.
3. **Provable Financial & Quota Accounting**: Authoritative token usage, reasoning/cache token breakdowns, dynamic routing aliases, and provider-reported generation identifiers are extracted and persisted in the immutable usage ledger.
4. **Safe Transport**: Async HTTP/2 with strict TLS, fixed hostnames (no dynamic base URLs), finite timeouts, redirect blocking, and backpressure-aware streaming.

---

## 2. Provider Specifications & Compatibility Matrix

| Provider | Endpoint Base | Authentication | Default Free / Low-Cost Model | Streaming Protocol | Usage Frame Handling | Known Rate Limits & Quotas | Official Documentation Reference |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Google Gemini** | `https://generativelanguage.googleapis.com/v1beta/models/` | Header: `x-goog-api-key: <KEY>` | `gemini-1.5-flash`, `gemini-2.0-flash` | `:streamGenerateContent?alt=sse` | `usageMetadata` in final chunk (`promptTokenCount`, `candidatesTokenCount`, `totalTokenCount`) | Free tier: 15 RPM, 1M TPM, 1,500 RPD | [Google AI Studio REST Docs](https://ai.google.dev/api/generate-content) (Feb 2026) |
| **Groq** | `https://api.groq.com/openai/v1/` | Header: `Authorization: Bearer <KEY>` | `llama-3.3-70b-versatile`, `llama-3.1-8b-instant` | `chat/completions` (OpenAI-compatible SSE) | `stream_options.include_usage: true`; final chunk provides `usage` object before `[DONE]` | Free tier: 30 RPM, 6K-30K TPM, 14.4K RPD, 500K TPD (Llama 3.3) | [Groq API Reference](https://console.groq.com/docs/api-reference) (Feb 2026) |
| **OpenRouter** | `https://openrouter.ai/api/v1/` | Header: `Authorization: Bearer <KEY>`, `HTTP-Referer`, `X-Title` | `openrouter/free` (Dynamic Alias) | `chat/completions` (OpenAI-compatible SSE) | `stream_options.include_usage: true`; final chunk provides `usage` and resolved `model` | Dynamic per upstream provider; 20 RPM free tier | [OpenRouter API Docs](https://openrouter.ai/docs) (Feb 2026) |
| **Cerebras** | `https://api.cerebras.ai/v1/` | Header: `Authorization: Bearer <KEY>` | `llama3.1-8b`, `llama-3.3-70b` | `chat/completions` (OpenAI-compatible SSE) | `stream_options.include_usage: true`; reasoning tokens in `completion_tokens_details` | Free tier: 30 RPM, 60K TPM, 14.4K RPD, 1M TPD | [Cerebras Inference API](https://inference-docs.cerebras.ai/) (Feb 2026) |

---

## 3. Detailed Provider Adapter Protocols

### 3.1 Google Gemini (Native REST & SSE)

#### 3.1.1 Endpoints & Authentication
- **Base Host**: `generativelanguage.googleapis.com` (HTTPS only).
- **Streaming URL**: `https://generativelanguage.googleapis.com/v1beta/models/{model_id}:streamGenerateContent?alt=sse`
- **Non-Streaming URL**: `https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent`
- **Authentication**: `x-goog-api-key: <VAULT_DECRYPTED_SECRET>` header.  
  *Security Note*: Under no circumstances is the key passed via URL query parameter (`?key=...`), preventing leakages into server logs, proxy access dumps, or referrer headers.

#### 3.1.2 Request Payload Transformation
Gemini expects a native payload structure differing from OpenAI:
```json
{
  "contents": [
    {
      "role": "user",
      "parts": [{"text": "Hello, world!"}]
    },
    {
      "role": "model",
      "parts": [{"text": "Hello! How can I help you?"}]
    }
  ],
  "systemInstruction": {
    "parts": [{"text": "You are a concise academic tutor."}]
  },
  "generationConfig": {
    "temperature": 0.7,
    "maxOutputTokens": 2048,
    "topP": 0.95
  }
}
```
- Message Role Mapping:
  - `"system"` messages are extracted and combined into the top-level `systemInstruction` field.
  - `"user"` messages map to `"user"`.
  - `"assistant"` messages map to `"model"`.
- Unsupported elements: Multimodal parts, tool declarations, and image buffers are rejected with HTTP 400 at the gateway before adapter dispatch.

#### 3.1.3 Streaming Response Parsing
Gemini SSE returns frames with `data: <JSON>`:
```json
data: {
  "candidates": [
    {
      "content": {
        "parts": [{"text": "Here is the response..."}],
        "role": "model"
      },
      "finishReason": "STOP",
      "index": 0
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 24,
    "candidatesTokenCount": 18,
    "totalTokenCount": 42
  }
}
```
- Finish Reason Mapping:
  - `"STOP"` $\to$ `"stop"`
  - `"MAX_TOKENS"` $\to$ `"length"`
  - `"SAFETY"`, `"RECITATION"` $\to$ `"content_filter"`
  - `"OTHER"`, `null` $\to$ `"unknown"`

#### 3.1.4 Error and Safety Mapping
- Safety Blocks: When Gemini blocks output due to content policy, candidate `finishReason` is `"SAFETY"` or `promptFeedback.blockReason` is populated (e.g. `"SAFETY"`, `"BLOCKLIST"`). Mapped to internal error category `content_safety_blocked`.
- Upstream HTTP Statuses:
  - `400 Bad Request` $\to$ `invalid_request`
  - `403 Forbidden` / `400 API_KEY_INVALID` $\to$ `invalid_credential`
  - `429 Too Many Requests` (RESOURCE_EXHAUSTED) $\to$ `provider_rate_limited` or `provider_quota_exhausted`. Reads `Retry-After` header or `details[].metadata.quota_reset` to set Redis cooldown.
  - `500` / `503` $\to$ `provider_unavailable`.

---

### 3.2 Groq (OpenAI-Compatible Chat Completions)

#### 3.2.1 Endpoints & Authentication
- **Base Host**: `api.groq.com` (HTTPS only).
- **Endpoint**: `https://api.groq.com/openai/v1/chat/completions`
- **Authentication**: `Authorization: Bearer <VAULT_DECRYPTED_SECRET>`

#### 3.2.2 Request Payload Transformation
Standard OpenAI chat completion payload with mandatory usage request:
```json
{
  "model": "llama-3.3-70b-versatile",
  "messages": [
    {"role": "system", "content": "You are a concise academic tutor."},
    {"role": "user", "content": "Explain quicksort."}
  ],
  "temperature": 0.7,
  "max_completion_tokens": 2048,
  "stream": true,
  "stream_options": {
    "include_usage": true
  }
}
```
*Note*: `stream_options: {"include_usage": true}` is required for Groq to stream the trailing usage frame.

#### 3.2.3 Streaming Response Parsing
- Chunks follow `data: {"id": "chatcmpl-...", "choices": [{"delta": {"content": "text"}, "finish_reason": null}]}`.
- Intermediate text deltas emit normalized `text_delta` events.
- Final chunk emits `finish_reason: "stop"` (or `"length"`), followed by an empty choices chunk containing:
```json
data: {
  "id": "chatcmpl-12345",
  "choices": [],
  "usage": {
    "prompt_tokens": 35,
    "completion_tokens": 120,
    "total_tokens": 155,
    "prompt_time": 0.005,
    "completion_time": 0.042,
    "total_time": 0.047
  }
}
```
- Emits terminal SSE frame `data: [DONE]`.

#### 3.2.4 Rate Limit Headers & Cooldown
- `x-ratelimit-remaining-requests`, `x-ratelimit-reset-requests`
- `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-tokens`
- `retry-after`: Parsed and stored into Redis cooldown key `ragbait:cooldown:provider:groq` or credential-specific key.

---

### 3.3 OpenRouter (Dynamic Routing & Aggregator Protocol)

#### 3.3.1 Endpoints & Authentication
- **Base Host**: `openrouter.ai` (HTTPS only).
- **Endpoint**: `https://openrouter.ai/api/v1/chat/completions`
- **Authentication**: `Authorization: Bearer <VAULT_DECRYPTED_SECRET>`
- **Additional Headers**: `HTTP-Referer: https://ragbait.app`, `X-Title: RagBait`

#### 3.3.2 Dynamic Alias Resolution (`openrouter/free`)
- In M2.1, `openrouter/free` was registered as a dynamic routing alias.
- When dispatching `openrouter/free`, the adapter sends `"model": "openrouter/free"`.
- In streaming chunks and the final response, OpenRouter returns the actual upstream model selected in the `model` property (e.g. `"model": "meta-llama/llama-3.2-3b-instruct:free"` or `"model": "mistralai/mistral-7b-instruct:free"`).
- The adapter extracts this field and records:
  - `requested_model_id = "openrouter/free"`
  - `resolved_model_id = chunk["model"]`
  - `upstream_provider = "openrouter"`

#### 3.3.3 Streaming & Usage
- Sends `stream: true` and `stream_options: {"include_usage": true}`.
- Usage is provided in the trailing chunk:
```json
data: {
  "id": "gen-...",
  "model": "meta-llama/llama-3.2-3b-instruct:free",
  "choices": [],
  "usage": {
    "prompt_tokens": 52,
    "completion_tokens": 80,
    "total_tokens": 132
  }
}
```
- Generation ID `gen-...` is recorded as `upstream_generation_id`.

---

### 3.4 Cerebras (Ultra-Fast Inference & Reasoning Token Extraction)

#### 3.4.1 Endpoints & Authentication
- **Base Host**: `api.cerebras.ai` (HTTPS only).
- **Endpoint**: `https://api.cerebras.ai/v1/chat/completions`
- **Authentication**: `Authorization: Bearer <VAULT_DECRYPTED_SECRET>`

#### 3.4.2 Request Payload Transformation
Standard OpenAI chat completions format with `stream: true` and `stream_options: {"include_usage": true}`.

#### 3.4.3 Reasoning and Cache Token Breakdown
Cerebras models (or reasoning-enabled variants) provide detailed token breakdowns:
```json
data: {
  "id": "chatcmpl-cerebras-...",
  "choices": [{"delta": {}, "finish_reason": "stop"}],
  "usage": {
    "prompt_tokens": 40,
    "completion_tokens": 90,
    "total_tokens": 130,
    "prompt_tokens_details": {
      "cached_tokens": 10
    },
    "completion_tokens_details": {
      "reasoning_tokens": 25
    }
  }
}
```
- **Integrity Rule**: `cached_tokens` (10) is a subset of `prompt_tokens` (40), and `reasoning_tokens` (25) is a subset of `completion_tokens` (90).
- Total billable/used tokens remain `total_tokens = 130` ($40 + 90$).
- Normalized usage records breakdowns without double counting.

---

## 4. Normalized SSE Event Contract

The frontend and downstream callers never receive raw provider chunks. The gateway transforms all upstream streams into five standard SSE events:

### Event 1: `generation_started`
Emitted immediately upon receiving the first valid stream byte:
```
event: generation_started
data: {"request_id": "req-12345", "reservation_id": "...", "model_id": "gemini-1.5-flash", "created_at": "2026-02-07T12:00:00Z"}
```

### Event 2: `text_delta`
Emitted for every incoming chunk containing incremental text content:
```
event: text_delta
data: {"request_id": "req-12345", "delta": "Quicksort is ", "index": 0}
```

### Event 3: `usage`
Emitted when authoritative or derived token counts are received:
```
event: usage
data: {
  "request_id": "req-12345",
  "usage": {
    "input_tokens": 30,
    "output_tokens": 150,
    "total_tokens": 180,
    "cached_tokens": 0,
    "reasoning_tokens": 0,
    "usage_source": "authoritative"
  },
  "cost_microcents": 0,
  "resolved_model_id": "gemini-1.5-flash",
  "upstream_generation_id": null
}
```

### Event 4: `generation_completed`
Emitted as the terminal normal event:
```
event: generation_completed
data: {"request_id": "req-12345", "finish_reason": "stop"}
```

### Event 5: `generation_error`
Emitted if a mid-stream failure occurs after HTTP 200 headers were already committed:
```
event: generation_error
data: {"request_id": "req-12345", "error_code": "upstream_timeout", "message": "The upstream inference provider timed out during generation"}
```
