# lion_mgmt Agent Guide

<!-- AUTONOMY DIRECTIVE — DO NOT REMOVE -->
YOU ARE AN AUTONOMOUS CODING AGENT. EXECUTE TASKS TO COMPLETION WITHOUT ASKING FOR PERMISSION.
DO NOT STOP TO ASK "SHOULD I PROCEED?" — PROCEED. DO NOT WAIT FOR CONFIRMATION ON OBVIOUS NEXT STEPS.
IF BLOCKED, TRY AN ALTERNATIVE APPROACH. ONLY ASK WHEN TRULY AMBIGUOUS OR DESTRUCTIVE.
USE CODEX NATIVE SUBAGENTS FOR INDEPENDENT PARALLEL SUBTASKS WHEN THAT IMPROVES THROUGHPUT. THIS IS COMPLEMENTARY TO OMX TEAM MODE.
<!-- END AUTONOMY DIRECTIVE -->

This repository is the AIOE backend management service. It is a Python 3.11 async Sanic application, not a TypeScript/Node project.

Use the fff MCP tools for all file search operations instead of default shell search tools.

## Product Shape

`lion_mgmt` manages the AIOE operational automation platform:

- Management foundation: users, tenants, roles, RBAC permissions, security policy, OAuth/JWT settings, platform config, license.
- Tool/application layer: ZIP tool package upload, tool versions, actions, resource templates, encrypted resource instances, action permissions.
- Automation layer: scenes, tags, playbooks, playbook versions, playbook permissions, scheduled execution, run logs, approvals.
- Event and observability layer: event ingestion, Vector config generation, Kafka/Syslog source handling, VictoriaLogs query, event linkage to playbooks.
- Collaboration layer: warrooms, WebSocket chat, run-log broadcast, AI replies, private/public rooms.
- Agent layer: lion_agent client, command center agent registry, task dispatch, task progress event stream, MCP SSE/messages endpoints.
- Inspection/reporting layer: inspection schedules, categories, targets, results, custom reports, dashboards and large-screen configuration.

## Technical Architecture

Runtime path:

```text
server.py
  -> services.create_app()
  -> core listeners / Tortoise / middleware / exception handlers / blueprints
  -> services/<module>/views.py
  -> libs/* and models/*
  -> PostgreSQL / Redis / Kafka / S3-MinIO / Vector / VictoriaLogs / LLM providers
```

Important entry points:

- `server.py` starts Sanic on `0.0.0.0:8000`.
- `services/__init__.py` owns `create_app()` and all blueprint registration.
- `settings/__init__.py` owns environment loading, logging, Redis cache config, Tortoise config, Kafka topics, object storage, lion_agent endpoints and LLM settings.
- `core/middlewares/security.py` owns authentication and audit-log publishing.
- `core/listeners/cache.py` initializes Redis/OAuth config and starts `ChatManager` background listeners.
- `models/base.py` defines `BaseModel`, soft delete, tenant scoping, and encrypted char fields.
- `services/digital_employee/lion_agent_client.py` wraps internal HTTP calls to lion_agent for agent execution and streaming.
- `services/command_center/dispatcher.py` uses lion_agent to choose an available agent for command-center tasks.

License-gated blueprints are registered dynamically in `services/__init__.py` from `load_license().moduleList`. When adding a licensed feature, keep the module key, permission menu, blueprint name, and license key aligned.

## Commands

Use these commands from the repository root:

```bash
uv sync
python server.py
aerich migrate
aerich upgrade
git diff --check
```

Container runtime:

```bash
docker build -t lion_mgmt .
```

The Docker image compiles `core/`, `libs/`, `models/`, and `services/` with Nuitka, copies migrations, settings and report templates, waits for PostgreSQL, runs `aerich upgrade`, then starts the service.

## Source Layout

- `core/`: framework glue, middleware, listeners, exception handlers, response helpers.
- `libs/`: shared infrastructure helpers for security, Kafka, chat, supervisor, storage and generic utilities.
- `models/`: Tortoise ORM models. Most models inherit `BaseModel` and include `id`, `ctime`, `utime`, `deleted`, `tenant_id`.
- `services/`: business modules. Each module usually exposes a Sanic `Blueprint` from `views.py`; complex logic can live in `logic.py`.
- `settings/`: process configuration and environment variable mapping.
- `migrations/`: Aerich migrations.

Do not add new application source files at repository root. Root is for entrypoints, deployment/config files and top-level documentation.

## Coding Rules

- Follow the existing async style. Database, Redis, Kafka, HTTP and file/storage operations are generally async and must be awaited.
- New APIs should return `response_ok(data)` on success and raise `CustomError(ErrorCode.X)` for business failures.
- New authenticated APIs should rely on `SecurityMiddleware` for session loading and use `@requires_permission("module:c|r|u|d")` for vertical permissions.
- Keep tenant isolation explicit. For ordinary business queries, include `tenant_id=request.ctx.session.get("tenant_id")` and `deleted=False` unless the model or endpoint is intentionally global.
- Use soft delete for `BaseModel` records unless the existing module clearly hard-deletes that entity.
- Use `in_transaction()` when a request mutates multiple related tables.
- Store sensitive resource values through `EncryptedCharField`, encrypted JSON helpers, environment variables, or deployment secrets. Do not hardcode credentials.
- When adding a business module, update `services/__init__.py`, permission definitions, license gating if needed, and migrations.
- When adding or changing playbook/tool/action execution, preserve `run_id` tracking and Kafka topic contracts.
- When changing agent runtime behavior, keep execution inside `lion_agent`; lion_mgmt should call it through `services/digital_employee/lion_agent_client.py`.

## Feature Boundaries

- User, role, tenant, secure, system config and license are the management foundation.
- `tools` owns uploaded tool packages, action metadata and resource binding.
- `workflow` owns playbook lifecycle, versions, publish state and playbook permissions.
- `timed` owns scheduled execution of playbooks/actions.
- `event_ingestion` owns source ingestion and Vector/VictoriaLogs configuration; `event` owns incident/event records and handling.
- `warroom` and `libs/chat.py` own realtime room/message state and WebSocket delivery.
- `ai`, `agent`, `command_center`, `mcp` and `services/digital_employee/lion_agent_client.py` own AI-facing and agent-facing workflows.
- `inspection` owns inspection schedules, categories, targets, results and report generation.

Keep edits inside the owning boundary unless the request truly crosses modules.

## Verification

Before claiming completion:

- For documentation-only changes, run `git diff --check` and inspect the diff.
- For Python code changes, run the smallest relevant import/compile check, for example `python -m compileall <changed paths>`, plus targeted runtime checks when external services are not required.
- For model changes, create and inspect an Aerich migration.
- For API behavior changes, verify authentication, tenant scoping, permission checks, response format and error paths.
- For Kafka/WebSocket/lion_agent changes, verify the event path and explain any external-service gap if it cannot run locally.

The repository currently does not expose a formal test suite in `tests/`; add focused tests or lightweight checks when changing risky shared behavior.

## Git And Workspace Safety

- The worktree may contain unrelated local files. Do not revert, delete or stage unrelated changes.
- Do not commit secrets, `.env` files, real license files, runtime data, caches or local OMX state.
- `.omx/` is locally excluded through `.git/info/exclude`; keep it out of commits.
- If asked to commit, use the repository's active commit instructions from the current runtime and include tested/not-tested evidence in the message.
