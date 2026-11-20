# RagBait — M2.7: Local Ollama Setup & Configuration Guide

This guide describes how to configure and use **Private Local Inference** in RagBait using your own local Ollama installation.

---

## 1. Architectural Privacy Guarantees

RagBait M2.7 implements **direct browser-to-local loopback inference**:
- **Direct Loopback**: The web application in your browser communicates directly with your local Ollama daemon at `http://127.0.0.1:11434`.
- **Zero Cloud Relay**: Prompts, system instructions, and generated completions **never** pass through, get proxied by, or get logged on RagBait's cloud servers or database.
- **Credential Isolation**: All requests to Ollama use `credentials: "omit"` and `redirect: "error"`. Your Supabase authentication cookies and JWT tokens are never sent to your local Ollama daemon.
- **Zero Cost / Non-Billable**: Local inference runs exclusively on your local hardware (CPU/GPU). It consumes zero cloud credits and does not affect your cloud spending caps.

---

## 2. Prerequisites & Installation

1. Download and install Ollama from [ollama.com](https://ollama.com).
2. Verify that Ollama is installed by opening your terminal and checking its version:
   ```bash
   ollama --version
   ```
3. Verify that the Ollama service is listening on port 11434:
   ```bash
   curl http://127.0.0.1:11434/api/version
   ```
   Expected response: `{"version":"0.x.x"}`.

---

## 3. Configuring Cross-Origin Resource Sharing (`OLLAMA_ORIGINS`)

Modern web browsers enforce strict Cross-Origin Resource Sharing (CORS) rules. Because the RagBait web application runs at `http://localhost:3000` (or your university's domain), your browser will block fetch requests to `http://127.0.0.1:11434` unless Ollama is told to permit that origin.

> [!IMPORTANT]
> Never use wildcard `*` in production environments. Explicitly configure only the RagBait web app origins.

### Windows (PowerShell)

1. Open PowerShell and set the user environment variable:
   ```powershell
   [System.Environment]::SetEnvironmentVariable(
     "OLLAMA_ORIGINS",
     "http://localhost:3000,http://127.0.0.1:3000",
     "User"
   )
   ```
2. **Crucial**: Right-click the Ollama icon in the Windows Taskbar Notification Area (system tray), select **Quit Ollama**, and relaunch Ollama from the Start Menu.

### macOS

1. Open Terminal and set the environment variable via `launchctl`:
   ```bash
   launchctl setenv OLLAMA_ORIGINS "http://localhost:3000,http://127.0.0.1:3000"
   ```
2. Quit the Ollama application completely (Cmd+Q) and relaunch it from Applications.

### Linux (systemd)

1. Edit the systemd service override:
   ```bash
   sudo systemctl edit ollama.service
   ```
2. Add the following lines under `[Service]`:
   ```ini
   [Service]
   Environment="OLLAMA_ORIGINS=http://localhost:3000,http://127.0.0.1:3000"
   ```
3. Save, reload systemd, and restart the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```

---

## 4. Downloading Recommended Models

RagBait automatically discovers all models installed in your local Ollama library. We recommend downloading one or more of the following models based on your hardware specifications:

### Lightweight Models (Fast / Lower RAM)
- **Llama 3.2 3B**: Excellent general-purpose assistant (~2.0 GB).
  ```bash
  ollama pull llama3.2:3b
  ```
- **Qwen 3 0.6B**: Ultra-lightweight reasoning model (~520 MB).
  ```bash
  ollama pull qwen3:0.6b
  ```

### Coding & Academic Models (8 GB+ VRAM recommended)
- **Qwen 2.5 Coder 7B**: Outstanding coding capabilities (~4.7 GB).
  ```bash
  ollama pull qwen2.5-coder:7b
  ```
- **Llama 3.1 8B**: Comprehensive knowledge and instruction following (~4.7 GB).
  ```bash
  ollama pull llama3.1:8b
  ```
- **DeepSeek R1 8B / 7B**: Advanced step-by-step reasoning (~4.7 GB).
  ```bash
  ollama pull deepseek-r1:8b
  ```

To list your downloaded models:
```bash
ollama list
```

---

## 5. Using Local Inference in RagBait

1. Log in to RagBait.
2. In the navigation bar, click **Local Inference** (or navigate to `/local-inference`).
3. Verify that the status banner shows **Ollama Connected** (green dot with version number).
4. Select your downloaded model from the **Local Model** dropdown.
5. (Optional) Click **Parameters** to adjust the system prompt or temperature.
6. Type your prompt into the message box and press **Enter** (or click **Send**).
7. Notice the real-time token streaming and inspect the **Device Telemetry** panel on the right, which shows:
   - Prompt evaluation tokens and duration.
   - Completion evaluation tokens and duration.
   - Generation throughput in tokens per second (`tok/s`).
   - Total latency.

---

## 6. Troubleshooting

### Problem: "Ollama Daemon Offline"
- **Cause**: Ollama is not running on your machine.
- **Solution**: Launch Ollama from your desktop application launcher or run `ollama serve` in a terminal window.

### Problem: "Ollama Reachable but Blocked by CORS (`OLLAMA_ORIGINS`)"
- **Cause**: Ollama is rejecting requests because the web application's origin is not in `OLLAMA_ORIGINS`.
- **Solution**: Follow the steps in Section 3 to set `OLLAMA_ORIGINS` and ensure you **completely restart** Ollama.

### Problem: No Models in Dropdown
- **Cause**: Ollama is running, but no models have been pulled yet.
- **Solution**: Run `ollama pull llama3.2` in your terminal and click **Refresh** in RagBait.

### Problem: Generation is Slow
- **Cause**: The model is executing on the CPU because GPU layers are not offloaded.
- **Solution**: Verify GPU drivers (NVIDIA CUDA, AMD ROCm, or Apple Metal). Ollama automatically detects supported GPUs on startup. Inspect Ollama server logs to confirm GPU layer offloading.
