# RagBait — M2.7: Local Ollama Setup, Local-Only Enforcement & Browser Security Guide

This document specifies the setup, security boundaries, and strict local-only configuration for **Private Local Inference** in RagBait.

---

## 1. Architectural Privacy Boundaries & Three-Tier Distinction

To accurately understand the privacy guarantees of M2.7, three distinct operational layers must be distinguished:

1. **Browser-to-Local Ollama Communication**:
   - The RagBait web application runs in the user's browser and communicates directly with `http://127.0.0.1:11434`.
   - All requests use `credentials: "omit"`, `redirect: "error"`, and declare `targetAddressSpace: "loopback"`.
   - **Guarantee**: Zero prompt or completion data is ever transmitted to RagBait cloud servers, databases, or Redis queues.
2. **Genuinely Local Model Execution**:
   - When Ollama runs a model downloaded to the local hard drive (e.g. `llama3.2:3b`, `qwen2.5-coder:7b` in GGUF format), weight tensors and inference computations execute 100% on local CPU/GPU cores.
   - **Guarantee**: Zero token data leaves your physical machine.
3. **Cloud-Hosted / Proxy Models**:
   - Modern Ollama distributions may allow integrating cloud-hosted models or online web search tools if configured.
   - **Limitation**: Ollama's local REST API (`/api/tags`) returns model tags, file sizes, digests, and architecture details, but **does not expose a standardized `remote` or `cloud` boolean field**. Therefore, the browser cannot reliably differentiate cloud-routed models from purely local GGUF models via metadata alone.
   - **Requirement**: To guarantee that no requests are forwarded to external cloud services, users seeking absolute local privacy **must** enforce local-only Ollama configuration using `OLLAMA_NO_CLOUD=1`.

> [!WARNING]
> Do NOT assume that every model listed in `ollama list` executes locally if your Ollama instance is configured with cloud proxies or upstream cloud endpoints. Enable `OLLAMA_NO_CLOUD=1` for guaranteed offline privacy.

---

## 2. Enforcing Strict Local-Only Ollama (`OLLAMA_NO_CLOUD=1`)

Per the official Ollama documentation ([ollama faq](https://github.com/ollama/ollama/blob/main/docs/faq.mdx)), Ollama supports disabling cloud model routing and online features through the `OLLAMA_NO_CLOUD` environment variable or server configuration `disable_ollama_cloud: true`.

### Windows Setup (PowerShell)
1. Open PowerShell and set the user environment variable:
   ```powershell
   [System.Environment]::SetEnvironmentVariable("OLLAMA_NO_CLOUD", "1", "User")
   [System.Environment]::SetEnvironmentVariable("OLLAMA_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000", "User")
   ```
2. **Restart Requirement**: Right-click the Ollama icon in the Windows Taskbar Notification Area (system tray), select **Quit Ollama**, and relaunch Ollama from the Start Menu.

### macOS Setup
1. In Terminal, configure environment variables via `launchctl`:
   ```bash
   launchctl setenv OLLAMA_NO_CLOUD "1"
   launchctl setenv OLLAMA_ORIGINS "http://localhost:3000,http://127.0.0.1:3000"
   ```
2. Quit the Ollama desktop application completely (Cmd+Q) and relaunch it.

### Linux Setup (systemd)
1. Edit the service override configuration:
   ```bash
   sudo systemctl edit ollama.service
   ```
2. Add the following lines under `[Service]`:
   ```ini
   [Service]
   Environment="OLLAMA_NO_CLOUD=1"
   Environment="OLLAMA_ORIGINS=http://localhost:3000,http://127.0.0.1:3000"
   ```
3. Reload systemd and restart Ollama:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```

---

## 3. Modern Chrome Local Network Access (LNA) Specification

Modern Chromium browsers (Chrome 130+) enforce **Local Network Access (LNA)** protections to prevent public internet websites from silently pivoting into local devices (`127.0.0.1`).

RagBait strictly adheres to the W3C Local Network Access specification:
1. **Secure Context Requirement**:
   - Browser loopback access is restricted to secure top-level contexts (`HTTPS`).
   - The browser exempts `http://localhost:3000` and `http://127.0.0.1:3000` as secure development contexts.
   - Non-secure HTTP access from custom hostnames or LAN IPs is rejected with an `insecure_context` notice.
2. **Explicit Address Space Declaration**:
   - Every fetch request issued by RagBait declares `targetAddressSpace: "loopback"` in a type-safe manner.
3. **Explicit User Gesture for Permission `prompt`**:
   - If `navigator.permissions.query({ name: "loopback-network" })` returns `prompt`, RagBait **never** fires an automatic request on page load.
   - A dedicated "Connect to Local Ollama" button is displayed. Clicking this button triggers the first local request within a genuine user gesture, allowing Chrome to display its permission prompt.
4. **Site Settings Remediation for `denied`**:
   - If the user previously clicked "Block" or site settings forbid loopback access, RagBait displays clear remediation steps directing the user to Chrome Site Settings (`chrome://settings/content/siteDetails`).

---

## 4. Network Security & Prohibited Configurations

To maintain the security guarantees of the student's workstation:
- **Loopback-Only Binding**: Ollama must remain bound to `127.0.0.1:11434`.
- **PROHIBITED**:
  - Never configure `OLLAMA_HOST=0.0.0.0` (exposes your Ollama instance to the entire local network or public internet without authentication).
  - Never use Ngrok, Cloudflare Tunnels, or reverse proxies to expose local Ollama to the public web.
  - Never port-forward port 11434 on your home or university router.
  - Never configure wildcard CORS (`OLLAMA_ORIGINS=*`).

---

## 5. Model Discovery & Recommended Local Models

Because RagBait queries `/api/tags` directly from your local Ollama daemon, any model you pull locally is instantly available in the dropdown selector.

### Recommended Local Models (Genuinely Offline GGUFs)
- **Llama 3.2 3B**: Fast general conversational assistant (~2.0 GB).
  ```bash
  ollama pull llama3.2:3b
  ```
- **Qwen 2.5 Coder 7B**: High-performance coding model (~4.7 GB).
  ```bash
  ollama pull qwen2.5-coder:7b
  ```
- **Qwen 3 0.6B**: Compact model (~520 MB).
  ```bash
  ollama pull qwen3:0.6b
  ```

---

## 6. Manual Real-Browser Verification Procedure

Because headless test runners (such as Vitest jsdom) cannot display real native Chromium permission dialogs, verify the browser behavior in Google Chrome as follows:

1. Launch Ollama locally:
   ```bash
   ollama serve
   ```
2. Verify Ollama responds to version check:
   ```bash
   curl http://127.0.0.1:11434/api/version
   ```
3. Start the RagBait web development server:
   ```bash
   pnpm --filter ragbait-web dev
   ```
4. Open Google Chrome and navigate to `http://localhost:3000/local-inference`.
5. **Initial Prompt Verification**:
   - If LNA is in `prompt` state, confirm that the blue banner appears: *"Browser Local Network Permission Required"*.
   - Confirm that no network error is thrown in the Chrome DevTools console before user interaction.
6. **User Gesture & Granting Verification**:
   - Click the blue button **Connect to Local Ollama**.
   - If prompted by Chrome to permit access to local network devices, click **Allow**.
   - Verify that the banner turns green: *"Ollama Connected v0.x.x (127.0.0.1:11434)"*.
   - Verify that your installed models appear in the **Local Model** dropdown.
7. **Inference & Thinking Omission Verification**:
   - Select a model (e.g. `qwen3:0.6b` or `llama3.2:3b`).
   - Send the prompt: `"Explain gravity in two sentences"`.
   - Confirm that streamed tokens render smoothly in the chat bubble.
   - Confirm that raw thinking or reasoning blocks are **NOT** rendered in the UI.
   - Confirm that the **Device Telemetry** card updates with prompt eval count, eval count, and generation throughput (`tok/s`).
8. **Denied State Verification**:
   - In Chrome, click the tune icon next to `localhost:3000` in the address bar $\to$ Site Settings $\to$ set "Insecure content" or "Local network" to **Block**.
   - Reload the page.
   - Confirm that the red banner appears: *"Loopback Access Blocked by Browser"*, with instructions to re-enable in Site Settings.
