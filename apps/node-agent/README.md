# RagBait Ollama Node Agent (`ragbait-node`)

The **RagBait Ollama Node Agent** allows approved students to voluntarily contribute their local computer's idle Ollama inference capacity to fellow RagBait students.

---

## Security & Privacy Guarantees

1. **Outbound-Only Connection**:
   The agent connects outbound to the RagBait server over WSS (WebSocket Secure). It never opens an inbound port and does not require port forwarding or public tunnels.
2. **Loopback Only**:
   The agent communicates strictly with your local Ollama daemon at `http://127.0.0.1:11434`.
3. **Local Private Key**:
   Your Ed25519 private key is generated locally on your machine (`~/.ragbait-node/node_key.pem`) with restricted filesystem permissions. It is never transmitted to RagBait.
4. **Server-Signed Authorizations**:
   The agent only executes inference jobs that carry an authentic cryptographic signature from RagBait's server, verifying the payload digest and model name.
5. **Contributor Control**:
   Sharing is disabled by default. You choose exactly which models are shared and configure concurrency and execution limits from the RagBait web interface.
6. **No Arbitrary Commands**:
   The agent contains zero remote shell, code execution, or file system access capabilities.

---

## Installation & Setup

### Prerequisites
- Python 3.12+ (or `uv`)
- Ollama running locally (`http://127.0.0.1:11434`)

### Setup with `uv`
```bash
cd apps/node-agent
uv sync
```

---

## CLI Usage

### 1. Pair your device
Obtain a 5-minute single-use pairing code from RagBait (`/community-nodes` in the web interface), then run:
```bash
uv run ragbait-node pair --code <YOUR-PAIRING-CODE>
```

### 2. Run the agent
Start the agent in the foreground:
```bash
uv run ragbait-node run
```

### 3. Check status
```bash
uv run ragbait-node status
```

### 4. Pause / Resume / Unpair
```bash
uv run ragbait-node pause
uv run ragbait-node resume
uv run ragbait-node unpair
```
