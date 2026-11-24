# Winston frontend

React and TypeScript chat interface powered by Vite.

The composer uses [`liquid-glass-react`](https://github.com/rdev/liquid-glass-react), distributed under the MIT license. Its displacement effect is browser-dependent; the translucent CSS surface remains as a fallback.

## Development

Start the backend from the `backend/` directory:

```bash
FEATURES__TOOLS_ENABLED=true \
TOOLS__PROJECT_ROOTS='["/home/you/Projects"]' \
uvicorn app.main:app --host 127.0.0.1
```

In another terminal, start the frontend from the `frontend/` directory:

```bash
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite binds to loopback and proxies `/health` and `/chat` requests, including confirmation actions, to `http://127.0.0.1:8000`.

The backend sends chat prompts to the configured local Ollama model. The project root setting discovers immediate non-hidden project directories and makes them available to Project Launcher. When tools are enabled, Winston can also propose code changes to its own repository; the diff must be applied explicitly, and backend tests require a separate approval. See the repository's `backend/.env.example` for configuration options.

Run the backend without `--reload` while using code proposals so applying a
patch does not discard its in-memory review state. Restart it manually after
the change is applied and tested.
