# Neural-Rip v4.0 — CLAUDE.md (Project Context)

## Project: Origami Protocol
**Target Platform:** macOS 15/16, Apple Silicon (M-series)
**Year:** 2026
**Trigger:** qBittorrent post-download hook (AppleScript → Python)

---

## Architecture Overview

### Compression Layer
- **Codec:** Zstandard (zstd) with dictionary-based learning
- **Parallelism:** Saturate all M-series Performance cores via `zstd --threads=0`
- All archive work lives in `/scripts/compress.py`

### Hydration Layer (CoreML / ANE)
- CoreML models stored in `/models/` (`.mlpackage` or `.mlmodelc`)
- The hydration pipeline reads low-bitrate VVC (H.266) frames and upscales to
  4K using a resident ANE model
- Model loading/inference lives in `/scripts/hydrate.py`

### HUD / UX
- Terminal-native UI via `rich` (pirate-themed, Scene-group aesthetic)
- Entry point: `neural_rip.py` in repo root

---

## Key Constraints for Claude Code
1. **ANE-first:** All ML inference must target `MLComputeUnits.cpuAndNeuralEngine`.
   Never fall back to GPU-only without a warning log.
2. **zstd multi-threading:** Always pass `threads` kwarg or `--threads=0` CLI flag.
3. **No blocking I/O on main thread** — use `asyncio` or `concurrent.futures`.
4. **Python ≥ 3.12**, venv at `.venv_rip/`.
5. Secrets / API keys go in `.env` (never committed).

---

## File Map
| Path | Purpose |
|------|---------|
| `neural_rip.py` | Main entry-point; HUD, argument parsing, orchestration |
| `scripts/compress.py` | zstd compress/decompress helpers |
| `scripts/hydrate.py` | CoreML ANE upscale pipeline |
| `scripts/qbt_hook.applescript` | qBittorrent post-processing trigger |
| `models/` | CoreML model bundles (.mlpackage) |
| `.cursorrules` | AI-agent constraints for Cursor |
| `.venv_rip/` | Python virtual environment (not committed) |
