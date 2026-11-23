# Winston frontend

React and TypeScript chat interface powered by Vite.

The composer uses [`liquid-glass-react`](https://github.com/rdev/liquid-glass-react), distributed under the MIT license. Its displacement effect is browser-dependent; the translucent CSS surface remains as a fallback.

## Development

Start the backend from the `backend/` directory:

```bash
uvicorn app.main:app --reload
```

In another terminal, start the frontend from the `frontend/` directory:

```bash
npm install
npm run dev
```

Open the URL Vite prints (usually `http://localhost:5173`). The Vite development server proxies `/health` and `/chat` requests to `http://127.0.0.1:8000`. Set `WINSTON_API_TARGET` to use another backend address.

The backend sends chat prompts to the configured local Ollama model. See the repository's `backend/.env.example` for configuration options.
