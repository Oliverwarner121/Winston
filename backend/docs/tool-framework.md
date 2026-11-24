# Winston tool ecosystem

The internal tool ecosystem centralizes discovery, input/output validation,
permission checks, confirmation, execution, and audit logging. Tools are never
called directly by the model.

## Implemented tools

| Category | Tools | Default boundary |
|---|---|---|
| `FILES` | `file_search`, `file_read`, `file_write`, `file_move` | Configured workspace roots only; dotfiles, Git metadata, credential paths, and common key files are excluded. Writes and moves require confirmation because they can modify source code. |
| `DEVELOPMENT` | `code_proposal`, `project_launcher`, `open_vscode`, `git`, `terminal` | Code Proposal drafts a repository-scoped diff; applying it and running tests are separate user-approved actions. Project Launcher and VS Code open without an approval prompt. Every Git operation and terminal command requires confirmation. Terminal still uses argv (no shell), an executable allowlist, a timeout, and output limits. |
| `SYSTEM` | `system_info`, `utc_clock` | Read-only machine metrics and UTC time. |
| `BROWSER` | `web_search`, `browser_read` | Search is opt-in and sends queries to DuckDuckGo. Browser navigation is opt-in, HTTPS-only, host-allowlisted, JavaScript-disabled, and read-only. |
| `DOCKER`, `HOMELAB` | `docker_status`, `docker_manage` | Only configured container names are visible/manageable. Docker actions do not prompt for confirmation and require the local `WRITE` permission. |
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
- `app.projects.registry` loads and validates the configured project list from
  `backend/config/projects.yaml`; `project_launcher` opens the configured
  applications through the central executor.
- `app.services.code_proposals` provides a supervised code-change lifecycle:
  gather bounded source context, draft a diff, wait for approval to apply it,
  then separately wait for approval to run the backend test suite.
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

The project launcher reads `TOOLS__PROJECTS_CONFIG_PATH`, which defaults to
`backend/config/projects.yaml`. Project paths may be absolute or relative to
that YAML file; they are expanded and checked for existence when the registry
loads. Additional project directories can be discovered one level below
configured roots:

```dotenv
TOOLS__PROJECT_ROOTS=["/home/you/Projects"]
```

Non-hidden real directories are registered using their directory names and
open in both VS Code and a terminal. Symlinked directories are skipped.
Alternatively, add explicit entries by project ID without changing Python
code:

```yaml
projects:
  family-blog:
    name: Family Blog
    path: ~/Projects/Family-Blog
    description: Weekly family update platform
    launch:
      vscode: true
      terminal: true
    startup_commands: []
    metadata: {}
```

The configured actions require the existing `READ` permission and do not
prompt for confirmation. The launcher starts VS Code and the first available supported
desktop terminal (`gnome-terminal`, `x-terminal-emulator`, `konsole`, or
`xfce4-terminal`) in the configured project directory. `startup_commands` and
`metadata` are preserved as extension points; startup commands are not run.

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

## Agent, local access, and confirmation

`FEATURES__TOOLS_ENABLED` enables tool planning in `/chat` and grants the
fixed local actor the `WRITE` level. Tool planning and confirmation requests
are accepted only from loopback clients and loopback browser origins. Bind the
backend and Vite frontend to `127.0.0.1`; do not expose either service through
a LAN address or reverse proxy. This local-only mode is not user
authentication: other processes running on the same machine can make requests
and respond to confirmations.

The chat UI displays the exact action summary and requires an explicit
Approve/Cancel choice only for code proposals, terminal commands, Git
operations, and file writes/moves. Opening projects or VS Code, web research,
and allowlisted Docker actions do not require approval prompts. Confirmation
requests are short-lived, actor-bound,
single-use, and permissions are rechecked before execution. Pending approvals
are held in process memory and do not survive restarts or work across multiple
workers.

The fixed local actor is not supplied by the model or request body. For
multi-user or remote access, replace this local-only mode with real
authentication and per-user permission grants before exposing tool actions.

## Supervised coding

When tools are enabled, Winston can draft changes only under approved source
directories in its own repository (`backend/app`, `backend/tests`,
`backend/docs`, `backend/config`, `frontend/src`, and the root README). It
cannot edit dependency manifests, environment files, arbitrary files elsewhere
on the PC, or delete files through this workflow. The proposed diff is shown
before an explicit Apply action; the server checks that source files have not
changed since drafting and applies only that diff. Applying does not commit or
directly restart Winston (a development server using `--reload` can reload on
source changes, so run without it during code proposals and restart manually).

Running tests is a separate action after applying. The button runs only
`python -m pytest -q` from the backend directory, with a timeout and bounded
output. Tests execute as Winston's OS user and may have that user's filesystem
and network access; review the patch and test files before approving. No
package installation or arbitrary model-generated command is run.

## Adding a tool

Create Pydantic input/output models, implement the typed interface, then
register the implementation without changing the executor:

```python
registry.register(WeatherTool())
```

Do not auto-import or load untrusted plugin packages. Tool implementations
should validate paths and external identifiers themselves, declare the
minimum required permission, and follow the approval boundary above: code
changes, terminal commands, Git operations, and file writes/moves require
confirmation; opening, searching, and allowlisted Docker actions do not.

## Tests

Run the backend tests from the repository root:

```bash
backend/.venv/bin/python -m pytest -q
```
