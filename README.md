# Winston

> A self-hosted AI assistant designed to run on local hardware and act as a central interface for personal productivity, project management, development workflows, and homelab operations.

## Overview

Winston is a personal project focused on building a privacy-first AI assistant that can understand requests, retrieve information, and perform real-world actions through an extensible tool framework.

Rather than being a chatbot, Winston is intended to become a personal operating system for managing projects, knowledge, infrastructure, and everyday tasks.

## Capabilities

- Local AI-powered conversations
- Project and workspace management
- Personal knowledge base and memory
- Email and calendar assistance
- Task and productivity management
- Homelab monitoring and administration
- Voice interaction
- Workflow automation
- Tool and service integrations
- Multi-device access


## Technology Stack

- Python
- FastAPI
- TypeScript
- React
- Docker
- Ollama
- Local Language Models

## Development

The React frontend lives in [`frontend/`](./frontend/README.md). Run the FastAPI backend and Vite frontend in separate terminals as described in the frontend guide.

The chat endpoint connects to the configured local Ollama model. See [`backend/.env.example`](./backend/.env.example) for the model URL, model name, and timeout settings. Run `uvicorn app.main:app --reload` from `backend/`, then test the chat API with:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello Winston"}'
```

## Security auditing

This repository includes [Cloudflare's security-audit skill](./.github/skills/security-audit/SKILL.md) for Copilot-compatible coding agents. It is development tooling, not part of Winston's runtime. Before requesting a full audit, review the skill's sandbox requirements; it requires an OS-enforced sandbox before running target-controlled code.

The skill is vendored from [cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill/tree/c1c8a8c1471069fb0e188eeaff69b8e8db6564a8) under its [MIT license](./.github/skills/security-audit/LICENSE).

