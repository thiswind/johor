# JOHOR Agent

[English](README.md) · [中文](README.zh-CN.md) · [العربية](README.ar.md)

A single-user personal AI agent, purpose-built for **Telegram**.

JOHOR talks with one designated user — conversation with a persistent persona, vision, image generation, web search, voice in and out, music on request, scheduled jobs, and a growing skill system. It ships as one repository you can clone, configure, and run.

This is an independent product, not a configuration of its ancestor. It originated as a snapshot of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 0.13.0 (August 2026) and has since diverged deliberately: roughly 60% of the original tree was removed. JOHOR does not track upstream and evolves on its own.

## Why narrow beats general

hermes-agent is a general-purpose agent framework: dozens of messaging platforms, every option configurable, features averaged across all targets. That generality has a cost — when an adapter must satisfy twelve platforms, it can be excellent at none.

JOHOR made the opposite bet:

- **Telegram only.** One platform adapter, tuned hard: media handling, voice notes, long messages, edit-after-send.
- **One designated user.** No multi-tenant anything. Session, memory, and persona state assume a single human.
- **A short, opinionated capability stack** (below), each link production-tested daily.

Telegram itself earns the exclusivity: free bot API, generous message and file limits, native voice notes, first-class media, no platform tax, and a real client on every OS. For a personal agent, it is simply the best substrate.

What was cut in the slimming: 19 unused platform adapters, the docs site, the optional-skills catalog, the bundled TUI — all gone. What remains is what a Telegram personal agent actually runs.

## Capabilities

| Capability | What the user sees |
|---|---|
| Conversation | Long-form chat with a persistent persona; automatic context compaction, no mid-conversation amnesia |
| Vision | Send a photo or screenshot, ask about it |
| Image generation | Describe an image, get it back |
| Web search | Live retrieval with cited sources |
| Voice (STT) | Send voice notes, they are transcribed and understood |
| Voice (TTS) | Replies can be synthesized to audio |
| Music | Describe style/mood/language, get a full track in a minute or two |
| Scheduled jobs | Natural-language reminders and recurring checks |
| Skills | Loadable skill documents; the agent can learn new ones |

## Model requirements (read this first)

JOHOR is tool-heavy: a typical turn chains several tool calls under long-context instruction following. **The main model is the single biggest factor in how well this works.** A weak model will produce visibly degraded tool use — missed arguments, forgotten plans, broken loops.

Minimum bar for the main model (any one of):

| Provider | Minimum |
|---|---|
| Anthropic | Claude Opus 5 (`claude-opus-5`) or newer |
| OpenAI | GPT-6 Astra (`gpt-6-astra`) or newer |
| DeepSeek | DeepSeek-V4.1 series or newer |
| Zhipu (Z.ai) | GLM-5.3 or newer |

Rule of thumb: if a model cannot run an agentic tool-calling harness cleanly, it is below the bar. Anything at or above these tiers works well.

## The supported stack

This is a **curated build**: the services below are the ones selected, wired, optimized, and daily-tested. What is not listed is not supported — there is no long tail of half-working integrations to discover, and nothing is queued for future support. If a capability matters to this agent, it is on this list; if it is not on this list, this agent does not do it.

| Role | Service / model |
|---|---|
| Main model | GLM-5.3 (Anthropic-protocol endpoint) |
| Vision + auxiliary tasks | GLM-5.3-Flash |
| Image generation | gpt-image-2 |
| Web search | **Perplexity (sonar) — the first-class backend, no peer** |
| Web page extraction | Tavily |
| Speech-to-text | scribe_v3 |
| Text-to-speech | eleven_v4 (Rachel voice) |
| Music generation | Suno |
| Context engine | cursor_style (built in; see below) |

The main model is swappable (any provider meeting the bar in "Model requirements" above works through the same endpoint configuration), but the multimodal services are what this build is optimized against.

On search, one opinion is deliberate: **Perplexity is the only first-class search backend.** This agent is used for serious work, and serious work needs retrieval quality — keyword-era engines return junk dressed as answers. Other backends remain technically configurable, but they are second-class and treated as such.

Whether you own API keys for any of these is your business. The code does not care where a key came from.

### Sourcing keys without collecting vendors

Buying directly from every vendor above means several accounts and invoices. Two aggregator platforms cover most of the stack with a single account and key:

- [OpenRouter](https://openrouter.ai) — broad model catalog, one bill
- [apinexus.pro](https://apinexus.pro) — one-stop access to the multimodal services above

Either way, plug the resulting base URL + key into `config.yaml` and you are running the supported stack.

## Quickstart

```bash
git clone https://github.com/thiswind/johor
cd johor
uv sync                                   # build the env from the lockfile
cp config.example.yaml ~/.hermes/config.yaml
$EDITOR ~/.hermes/.env                    # fill in your API keys
python -m hermes_cli.main gateway run     # start the Telegram gateway
```

You need a Telegram bot token from [@BotFather](https://t.me/BotFather); everything else is keyed in `.env`. `config.example.yaml` documents every section with sane public defaults — endpoints, model names, and switches — with secrets left empty for you.

The public tree is the same code that runs in production, minus credentials and private deployment details. If it does not clone-and-run, that is a bug.

## Context engine: cursor_style

Long conversations are compacted by a Cursor-style engine (`plugins/context_engine/cursor_style/`, the repo default):

- protects only the system prompt — every turn is compactable, so the topic can actually drift instead of being dragged back to turn one
- tiktoken-exact counting with CJK awareness (2.5–2.9× more accurate than naive `chars//4` for Chinese/Arabic)
- full pre-compaction transcript archived to JSONL — summaries link back, details stay retrievable
- tool-output pruning, incremental summaries, anti-thrash cooldown

The engine is developed in the open at [hermes-cursor-compressor](https://github.com/thiswind/hermes-cursor-compressor); this repo vendors a copy as its built-in default.

## Security model

The reference deployment runs the agent under a systemd hard-sandbox: `ProtectSystem=strict` with a single writable home path, `NoNewPrivileges`, empty capability set, seccomp `@system-service`. A sanitized unit file ships in `customizations/` (in private deployments) — the public tree includes the sandbox unit template under `customizations/johor-agent.service`. Package installation is pinned (`uv.lock`, exact versions) and self-update channels are disabled by design.

## Testing

```bash
scripts/run_tests.sh          # full suite (Linux/WSL recommended)
scripts/run_tests.sh tests/agent/ -q
```

Only live production paths carry tests (tools/web, music, image, TTS, STT, gateway/telegram, agent core); tests for removed platforms were removed with them.

## Relationship to upstream / license

Snapshot of hermes-agent 0.13.0 (2026-08-29, single-commit baseline, no shared git history, no upstream tracking). Baseline customizations: Perplexity registry fallback + backend-drift alarm, the cursor_style engine, deployment reference files. Divergence since: brand rename, reconnect hardening, TTS migration, music generation, the slimming surgery, cursor_style promoted to default.

Licensed under **GPL-3.0** ([LICENSE](LICENSE)); the upstream MIT notice is preserved at [LICENSE.upstream-mit](LICENSE.upstream-mit). Credit to Nous Research for the foundation.

## Status

Actively maintained — this tree runs daily as someone's actual personal agent. The engine and stack are stable; development is incremental (bounds, tests, docs). Public releases are desensitized snapshots of the private production repo, cut when meaningful milestones land.

### Changelog

- **2026-11 (v2 snapshot)** — first full re-cut: slimmed tree (≈60% smaller than the 2026-08 baseline), cursor_style as default context engine, rewritten README, `config.example.yaml`, fresh single-commit history.
- **2026-08 (v1 snapshot)** — initial public snapshot.
