# RagBait — M2.7 Implementation Plan: Private Local Inference with Ollama

## 1. Overview and Core Philosophy

Milestone M2.7 introduces **Private Local Inference** to RagBait, allowing authenticated students and contributors to leverage their own locally running Ollama instances (`http://127.0.0.1:11434`) for zero-cost, fully private AI inference.

### Core Architectural Mandates:
1. **Direct Browser-to-Local Loopback**:
   The web application running in the student's browser communicates directly with `http://127.0.0.1:11434`. All prompts, system messages, and generated completions travel strictly between the user's browser runtime and their local machine's Ollama daemon.
2. **Zero Cloud Backend Involvement**:
   No local inference request or response is ever routed through, proxied by, or reported to the cloud FastAPI backend.
3. **Zero Persistence of Private Content**:
   No local prompt or completion is persisted in PostgreSQL, Supabase, Redis, or local storage.
4. **Zero Billing & Non-Attribution**:
   Local inference does not consume cloud budgets, does not mutate the M2.4 usage ledger, and does not award contributor community points.
5. **Strict Fixed Endpoint**:
   The endpoint is hardcoded to `http://127.0.0.1:11434`. No arbitrary URLs, LAN IPs, cloud proxies, tunnels, or user-configurable hosts are permitted.
6. **Isolated Credentials**:
   Browser fetch requests to Ollama strictly enforce `redirect: "error"` and `credentials: "omit"`. No Supabase JWTs, cookies, or auth headers are transmitted to the local Ollama daemon.

---

## 2. Directory and File Layout

```
apps/web/
  src/
    app/
      (protected)/
        local-inference/
          page.tsx                     # Protected Local Inference Playground page
    components/
      Navbar.tsx                       # Navigation bar with link to Local Inference
    lib/
      local-inference/
        ollama-types.ts                # TypeScript interfaces for Ollama REST & NDJSON API
        ollama-errors.ts               # Categorized error classes (CORS, offline, timeout, abort)
        ollama-stream.ts               # Resilient NDJSON stream parser with UTF-8 reassembly
        ollama-client.ts               # Isolated client targeting http://127.0.0.1:11434
  tests/
    local-inference/
      ollama-stream.test.ts            # Unit tests for streaming NDJSON parsing & edge cases
      ollama-client.test.ts            # Unit tests for client API methods & fetch configurations
      LocalInferencePage.test.tsx      # Integration tests for UI states, stream rendering, and aborts
docs/
  M2_7_IMPLEMENTATION_PLAN.md          # This implementation plan
  M2_7_OLLAMA_SETUP.md                 # Complete Ollama & OLLAMA_ORIGINS setup guide for Win/Mac/Linux
  M2_API.md                            # Updated with section on local inference client architecture
  M2_ARCHITECTURE.md                   # Updated with browser-to-loopback architectural diagram
  M2_SECURITY.md                       # Updated with loopback boundary security analysis
```

---

## 3. Detailed Component Specifications

### 3.1 TypeScript Definitions (`ollama-types.ts`)
- `OllamaVersionResponse`: `{ version: string }`
- `OllamaModelDetails`: `{ parent_model?: string; format?: string; family?: string; families?: string[]; parameter_size?: string; quantization_level?: string }`
- `OllamaTagModel`: `{ name: string; model: string; modified_at: string; size: number; digest: string; details: OllamaModelDetails; capabilities?: string[] }`
- `OllamaTagsResponse`: `{ models: OllamaTagModel[] }`
- `OllamaShowResponse`: `{ license?: string; modelfile?: string; parameters?: string; template?: string; details: OllamaModelDetails; model_info?: Record<string, unknown> }`
- `OllamaChatMessage`: `{ role: "system" | "user" | "assistant"; content: string; images?: string[] }`
- `OllamaChatOptions`: `{ temperature?: number; top_p?: number; top_k?: number; num_predict?: number }`
- `OllamaChatRequest`: `{ model: string; messages: OllamaChatMessage[]; stream?: boolean; options?: OllamaChatOptions }`
- `OllamaChatChunk`:
  - `model: string`
  - `created_at: string`
  - `message?: { role: string; content: string; thinking?: string }`
  - `done: boolean`
  - `done_reason?: string`
  - Performance statistics on final chunk:
    - `total_duration?: number` (nanoseconds)
    - `load_duration?: number` (nanoseconds)
    - `prompt_eval_count?: number`
    - `prompt_eval_duration?: number`
    - `eval_count?: number`
    - `eval_duration?: number`
- `OllamaUsageStats`: Normalized representation with computed speeds (`tokensPerSecond`, durations in ms/s).
- `OllamaConnectionState`: `'checking' | 'connected' | 'disconnected' | 'cors_blocked'`

### 3.2 Error Handling (`ollama-errors.ts`)
- Custom error hierarchy deriving from `Error`:
  - `OllamaError` base class with `code: string` and optional `cause`.
  - Codes:
    - `OLLAMA_UNREACHABLE`: Ollama daemon not running or not listening on 127.0.0.1:11434.
    - `CORS_BLOCKED`: Network error likely caused by missing or restrictive `OLLAMA_ORIGINS`.
    - `MODEL_NOT_FOUND`: Specified model tag is not installed locally.
    - `STREAM_ERROR`: Network stream interrupted prematurely.
    - `ABORTED`: User deliberately cancelled generation.
    - `PARSE_ERROR`: Malformed JSON chunk in Ollama response.

### 3.3 NDJSON Stream Parser (`ollama-stream.ts`)
- Function: `parseNDJSONStream<T>(stream: ReadableStream<Uint8Array>, onChunk: (data: T) => void, signal?: AbortSignal): Promise<void>`
- Logic:
  - Uses `TextDecoder("utf-8", { fatal: false })` with streaming enabled.
  - Maintains a line buffer.
  - Handles chunks containing multiple newlines (`\n` or `\r\n`).
  - Handles incomplete JSON strings split across multiple byte chunks.
  - Handles multi-byte Unicode sequences sliced across chunk boundaries.
  - Flushes any trailing valid line when stream closes.
  - Immediately aborts without uncaught exceptions if `signal.aborted` triggers.

### 3.4 Ollama Client (`ollama-client.ts`)
- Constant: `export const OLLAMA_BASE_URL = "http://127.0.0.1:11434";`
- Safe fetch wrapper:
  ```ts
  function safeOllamaFetch(path: string, init?: RequestInit): Promise<Response> {
    return fetch(`${OLLAMA_BASE_URL}${path}`, {
      ...init,
      mode: "cors",
      credentials: "omit",
      redirect: "error",
      cache: "no-store",
    });
  }
  ```
- Methods:
  - `checkConnection(signal?: AbortSignal): Promise<{ state: OllamaConnectionState; version?: string; error?: string }>`
  - `listModels(signal?: AbortSignal): Promise<OllamaTagModel[]>`
  - `showModel(name: string, signal?: AbortSignal): Promise<OllamaShowResponse | null>`
  - `chatStream(request: OllamaChatRequest, callbacks: { onDelta: (text: string) => void; onThinkingDelta?: (text: string) => void; onStats: (stats: OllamaUsageStats) => void }, signal?: AbortSignal): Promise<void>`

### 3.5 User Interface (`page.tsx`)
- Protected route: verifies user session and status.
- Header & Status:
  - Connection pill badge (Connected with version / Offline / CORS Blocked).
  - Refresh button.
- Model Selection & Config:
  - Dropdown listing local models with format, family, parameter size badges.
  - Empty state with CLI instructions to pull a model if list is empty.
  - Expandable parameter drawer (System prompt, Temperature slider).
- Playground & Chat Interface:
  - Session message history (in React memory only, never stored in DB or localStorage).
  - Input textarea with send on Enter (Shift+Enter for newline).
  - Generation controls: "Send", "Stop Generation" (cancels stream), "Clear Chat".
  - Markdown-friendly or plain-text response display with streaming cursor and separate thought/reasoning section if returned by model.
- Metrics & Non-Billable Status:
  - Prominent badge: "Local Hardware Execution — 100% Private & Free".
  - Statistics cards: Prompt Tokens, Output Tokens, Speed (tokens/s), Response Duration.
- Setup Guide Modal/Accordion:
  - Full instructions on configuring `OLLAMA_ORIGINS` for Windows, macOS, and Linux.

---

## 4. Verification & Testing Strategy

1. **Unit Testing (`ollama-stream.test.ts`)**:
   - Stream with multiple JSON objects in single chunk.
   - Stream with JSON split across 2+ chunks.
   - Multibyte emoji/Chinese characters split across chunk boundaries.
   - CRLF and LF mixed line endings.
   - AbortController cancellation during active stream.
   - Trailing non-newline chunk.
2. **Unit Testing (`ollama-client.test.ts`)**:
   - `checkConnection` handling success, 404, connection failure, and CORS failure.
   - `listModels` and `showModel` parsing.
   - `chatStream` invoking callbacks on text and statistics.
   - Verifying `credentials: "omit"` and `redirect: "error"` on all network requests.
   - Verifying zero authorization headers.
3. **Integration Testing (`LocalInferencePage.test.tsx`)**:
   - Renders checking/offline/connected states properly.
   - Populates model selector from mocked `listModels`.
   - Submitting prompt calls `chatStream` and renders streamed text.
   - Clicking "Stop" triggers abort signal and stops loading state.
   - Clicking "Clear" resets conversation state.
4. **Full System Verification**:
   - Frontend: `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build`.
   - Backend: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`, `uv run pytest -q`.
   - Supabase: `npx supabase test db`.
