# Winston tool ecosystem

The internal tool ecosystem centralizes discovery, input/output validation,
permission checks, confirmation, execution, and audit logging. Tools are never
called directly by the model.

## Implemented tools

| Category | Tools | Default boundary |
|---|---|---|
| `FILES` | `file_search`, `file_read`, `file_write`, `file_move` | Configured workspace roots only; dotfiles, Git metadata, credential paths, and common key files are excluded. Writes and moves require confirmation. |
| `DEVELOPMENT` | `open_vscode`, `git`, `terminal` | VS Code and Git operate on workspace paths. Terminal uses argv (no shell), an executable allowlist, a timeout, output limits, and confirmation. |
| `SYSTEM` | `system_info`, `utc_clock` | Read-only machine metrics and UTC time. |
| `BROWSER` | `web_search`, `browser_read` | Search is opt-in and sends queries to DuckDuckGo. Browser navigation is opt-in, HTTPS-only, host-allowlisted, JavaScript-disabled, and read-only. |
| `DOCKER`, `HOMELAB` | `docker_status`, `docker_manage` | Only configured container names are visible/manageable. Start, stop, restart, and logs require confirmation. |
| `PRODUCTIVITY` | Category reserved for plugins | Email, calendar, and task integrations are not implemented yet. |

`ToolCategory` also defines the `PRODUCTIVITY` and `HOMELAB` extension points.
Tools not listed above (for example email, calendar, Home Assistant, and
finance integrations) remain future plugins.

## Framework

- `app.tools.base` defines the generic typed tool contract, execution context,
  dynamic permission/confirmation hooks, and safe tool errors.
- `app.tools.models` defines capability metadata, categories, responses, and
  audit events.
- `app.tools.registry` registers tools by unique ID and lists schemas/categories.
- `app.tools.permissions` provides hierarchical READ, WRITE, ADMIN, and SYSTEM
  grants. Grants are explicit; the runtime starts with no actor permissions.
- `app.tools.executor` is the only supported execution path. It validates
  inputs/outputs, evaluates permissions, pauses for confirmation, measures
  duration, handles failures, and audits attempts and outcomes.
- `app.tools.audit` emits structured JSON records through `winston.audit`.
- `app.tools.runtime` composes the standard tools and their shared services.
- `app.services.tool_agent` provides bounded, one-tool-at-a-time planning. It
  filters tools by actor permission and delegates every call to the executor.

Input values are redacted in audit records by default. Implementations may opt
specific non-secret fields into audit output. Tool exceptions only reach
callers as safe messages; unexpected exceptions do not expose stack traces.

## Configuration and safe operation

Settings are nested under `TOOLS__` in `backend/.env`. See
[`../.env.example`](../.env.example). The default workspace is the Winston
repository. Configure additional roots with a JSON array, for example:

```dotenv
TOOLS__WORKSPACE_ROOTS=["/home/you/Projects","/home/you/Documents"]
```

The following integrations are disabled or restricted until explicitly
configured:

```dotenv
FEATURES__TOOLS_ENABLED=true
TOOLS__WEB_SEARCH_ENABLED=true
TOOLS__BROWSER_ALLOWED_HOSTS=["docs.python.org"]
TOOLS__GIT_ALLOWED_REMOTES=["origin"]
TOOLS__DOCKER_ALLOWED_CONTAINERS=["vaultwarden"]
TOOLS__TERMINAL_ALLOWED_PROGRAMS=["pytest"]
```

The terminal allowlist contains executable names, not shell fragments. Calls
are launched without a shell, inherit only a minimal environment, run inside a
workspace, and have bounded time and output. Git does not stage files
automatically; commits include only already-staged changes. Push/pull require
an allowlisted remote, and pull is fast-forward-only. No force-push operation
is provided.

Web search sends the search query to DuckDuckGo when enabled. Browser hosts
must be exact configured hostnames; private/reserved IP addresses and
non-HTTPS URLs are blocked, and redirects/subresources are checked against the
same host policy. The browser does not fill forms or submit actions. Install
the Chromium runtime after installing Python dependencies with:

```bash
cd backend
.venv/bin/python -m playwright install chromium
```

Docker commands use the configured local Docker CLI context named `default`
and only the names in `TOOLS__DOCKER_ALLOWED_CONTAINERS`.

## Agent, identity, and confirmation

`FEATURES__TOOLS_ENABLED` controls creation of the internal `ToolAgent`. No
tool execution or confirmation HTTP endpoint is exposed. The existing chat
route has no authenticated identity or confirmation UI, so it is intentionally
not wired to the agent. Do not pass an actor ID supplied by the model or an
unauthenticated request as trusted identity.

A future authenticated caller can use the runtime and agent in-process:

```python
runtime = request.app.state.tool_runtime
runtime.permission_manager.grant(authenticated_user_id, PermissionLevel.READ)
response = await request.app.state.tool_agent.run(
    user_message,
    actor_id=authenticated_user_id,
)
```

Grant only the required level. Confirmation requests are short-lived,
actor-bound, single-use, and recheck permissions before execution. Use
`tool_agent.confirm(confirmation_id, actor_id=...)` or
`tool_agent.cancel(...)` from a trusted caller. Pending approvals are in
process memory and do not survive restarts or work across multiple workers.
Before exposing this flow to a UI or network, add authentication and a
confirmation screen that displays the exact tool-generated action summary.

## Adding a tool

Create Pydantic input/output models, implement the typed interface, then
register the implementation without changing the executor:

```python
registry.register(WeatherTool())
```

Do not auto-import or load untrusted plugin packages. Tool implementations
should validate paths and external identifiers themselves, declare the
minimum required permission, and require confirmation for consequential
changes.

## Tests

Run the backend tests from the repository root:

```bash
backend/.venv/bin/python -m pytest -q
```
