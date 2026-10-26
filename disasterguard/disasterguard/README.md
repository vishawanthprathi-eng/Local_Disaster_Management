# 🛡️ DisasterGuard — Offline AI Disaster Management

A multi-agent disaster-response assistant that runs **100% offline** on a laptop using
[Ollama](https://ollama.com) and Google's **Gemma** model. No cloud, no API keys, no internet
needed after the one-time model download — built for situations where networks are down.

## What it does

| Agent | Job | Uses Gemma? |
|---|---|---|
| **Triage** | Turns a plain-language report into structured data: disaster type, severity (1–4), location, people affected, needs | ✅ JSON mode, with rule-based fallback |
| **Resource** | Allocates ambulances, rescue teams, boats, shelter beds, food, water, generators; flags shortfalls; releases on resolve | rules (deterministic, auditable) |
| **Planner** | Writes a numbered action plan for the field commander | ✅ with template fallback |
| **Advisor** | Safety Q&A (first aid, flood, earthquake, cyclone, fire, heatwave) grounded in a local knowledge base (RAG) | ✅ with "show stored guidance" fallback |
| **Reporter** | Generates a situation report with an executive summary | ✅ with stats-only fallback |

**Graceful degradation:** if Ollama is stopped or the model isn't installed, every agent falls back to
rules, so the app never stops working. The header badge shows which mode you're in.

## Quick start

Requires **Python 3.9+** (no pip packages needed — standard library only) and Ollama.

```bash
# 1. Install Ollama from https://ollama.com, then (once, needs internet):
ollama pull gemma3:4b          # or gemma3:1b for low-RAM machines

# 2. Run (works offline from here on)
git clone <your-repo-url> disasterguard
cd disasterguard
ollama serve &                 # skip if Ollama already runs as a service
python run.py                  # open http://127.0.0.1:8000
```

### Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DG_MODEL` | `gemma3:4b` | Any Ollama model, e.g. `gemma3:1b`, `gemma3:12b`, `gemma2:2b` |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama address |
| `DG_HOST` / `DG_PORT` | `127.0.0.1` / `8000` | Use `DG_HOST=0.0.0.0` to share on a local Wi-Fi/hotspot |
| `DG_EMERGENCY` | `112` | Emergency number shown in advice |
| `DG_DB` | `data/disasterguard.db` | SQLite file |
| `DG_LLM_TIMEOUT` | `120` | Seconds to wait for the model |

## Project layout

```
run.py                  entry point
app/server.py           HTTP server + REST API (stdlib)
app/agents.py           Triage / Resource / Planner / Advisor / Reporter + orchestrator
app/llm.py              Ollama client
app/db.py               SQLite schema + seed resources
app/knowledge/*.md      offline safety knowledge base (edit/add your own .md files)
app/static/             dashboard UI (no CDN — works offline)
tests/                  python -m unittest discover -s tests -t .
```

## API

`GET /api/status` · `GET/POST /api/incidents` · `GET /api/incidents/<id>` · `POST /api/incidents/<id>/resolve`
· `GET /api/resources` · `POST /api/advise` · `GET /api/sitrep` · `GET /api/stats` · `GET /api/logs`

## Customising for your district

* Edit `SEED_RESOURCES` in `app/db.py` with your real fleet/stock (delete `data/disasterguard.db` to re-seed).
* Add local guidance (NDMA/SDMA advisories, shelter lists) as `.md` files in `app/knowledge/` using `# Title` and `## Section` headings.

## ⚠️ Safety note

AI output is decision *support*. Always verify life-critical actions with trained responders and call
your national emergency number (112 in India). The Advisor is restricted to the local knowledge base to reduce hallucination.

## Tests

```bash
python -m unittest discover -s tests -t .
```
Tests include a fake Ollama server, so they verify both the Gemma path and the offline fallback without needing a model.
