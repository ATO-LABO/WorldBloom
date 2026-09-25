---
ja_rev: "777992f08c86"
---
# GPU Guard

When a single GPU is shared between Ollama and llama-server (see [Setting up Bonsai 2 27B](llm-backends.md#bonsai2-llama-server)), you'd otherwise have to manually start the server before generating and unload the Ollama model yourself. Writing `output.gpu_guard` in `settings.json` hands that off to a built-in WorldBloom mechanism instead.

## What it does

With `gpu_guard` configured, the following happen automatically before generation:

- **Lease**: acquires a single machine-wide OS file lock (default `%LOCALAPPDATA%\WorldBloom`, changeable via the `WORLDBLOOM_GPU_LEASE_DIR` environment variable) before generating. If another process holds it, it waits until it's free, and fails with `GpuBusy` if it's still not free after waiting (it never waits indefinitely). If the process holding the lock crashes, the OS releases it automatically
- **Coordinating with Ollama**: before using `llama-server`, it observes Ollama's `/api/ps` twice; if idle (no active response), it automatically unloads it with `keep_alive: 0`. While it judges Ollama to be in use, it waits, and fails with `GpuBusy` once the lease deadline passes. The reverse direction (checking whether llama-server is running before using Ollama) does not wait — if it's active, this fails immediately with `GpuBusy` (whichever started first is given priority, preventing both from exhausting VRAM at once)
- **Auto-start / auto-stop**: if `llama-server`'s `base_url` already responds, that instance (started by a human) is used as-is and never stopped. If there's no response and `launch` (the startup command) is configured, it's started automatically, waiting up to `startup_seconds` (default 180) for it to come up. Once generation finishes, only a server it started itself is stopped automatically. If a server was left over from a previous crash, whichever side next acquires the lease shuts it down automatically
- **Thermal guard**: checks GPU temperature (`nvidia-smi`) before each generation; if it's at or above `pause_at` (default 78°C), it waits at `poll_seconds` (default 15 second) intervals until it drops to `resume_at` (default 70°C) or below. If it's still not down after `max_wait_seconds` (default 600 seconds), it stops waiting and continues with generation anyway (this does not count as a failure). On environments where temperature can't be read, it never waits at all

## Writing settings.json

```json
{
  "output": {
    "default_backend": "llama-server",
    "gpu_guard": {
      "thermal": {"pause_at": 78, "resume_at": 70, "poll_seconds": 15, "max_wait_seconds": 600},
      "ollama_base_url": "http://localhost:11434",
      "observe_seconds": 15
    },
    "llama-server": {
      "base_url": "http://127.0.0.1:8089",
      "model": "bonsai2-27b",
      "think": true,
      "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.0, "max_tokens": 8192},
      "launch": ["C:/path/to/llama-server.exe", "-m", "C:/path/to/Ternary-Bonsai-2-27B-PTQ1_0.gguf",
        "--alias", "bonsai2-27b", "-ngl", "99", "-np", "1", "-c", "10240", "-fa", "on",
        "--host", "127.0.0.1", "--port", "8089", "--reasoning-budget", "2048",
        "--reasoning-budget-message", "思考の上限に達した。ここで思考を終え、直ちに最終回答の本文だけを書く。"],
      "startup_seconds": 180
    }
  }
}
```

If the `gpu_guard` key is absent, nothing changes from before (it's a no-op that preserves existing behavior). If the key is present, even an empty object `{}` enables it and every field takes its default.

The first element of `launch` should be the executable (exe) itself, not a wrapper like a `.cmd` file. Cleanup of a leftover server identifies "itself" by matching the executable's name, so a wrapper would prevent proper cleanup.

## Checking on-screen

The "動作状況" (Status) icon in the screen header shows the GPU and local-AI status (GPU temperature, whether Ollama/llama-server are running, etc.).

## When you hit GpuBusy { #gpubusy }

This happens when one of "another process holds the GPU", "Ollama is in use", or "another llama-server is running" fails to resolve before the lease deadline. Wait a while and retry, or finish whatever's competing for it (another generation job, an Ollama chat session, etc.) and retry.
