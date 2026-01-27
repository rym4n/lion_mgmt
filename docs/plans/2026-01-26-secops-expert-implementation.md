# SecOpsExpert Agent Implementation Plan

## Goal
Implement SecOpsExpert, an intelligent security operations agent integrated into the lion_mgmt service. The agent will feature a 5-layer memory architecture, support multi-tenant isolation, and interact with users via warroom chat using Letta for agent orchestration and PostgreSQL (pgvector) for memory storage.

## Proposed Changes

1.  **Infrastructure**:
    *   Enable `vector` extension in PostgreSQL.
    *   Add `letta`, `pgvector`, `httpx` dependencies.
    *   Configure connection to external Letta Server and Embedding service.

2.  **Data Models**:
    *   `AgentMapping`: Maps Warrooms to Letta Agent IDs.
    *   `ArchivalKnowledge`: Stores long-term knowledge (SOPs, case learnings) with vector embeddings.
    *   `UserPreference`: Stores user-specific settings (notifications, skills).

3.  **Core Logic (`services/secops_expert`)**:
    *   `SecOpsExpertGateway`: Manages agent lifecycle, distributed locking (Redis), and message routing.
    *   `AgentLifecycleManager`: Handles hooks for Warroom creation/archival and event triggers.
    *   `Tools`: Implement `call_playbook`, `list_available_playbooks`, `submit_case_learning`.
    *   `Prompts`: Define System Prompt and Core Memory Schema.

4.  **API Endpoints**:
    *   `/secops-expert/chat`: Synchronous chat interface.
    *   `/secops-expert/chat/stream`: SSE streaming chat interface.
    *   `/secops-expert/state`: Fetch agent L1 memory state.
    *   Life-cycle and Knowledge base management endpoints.

5.  **Integration**:
    *   Register `secops_expert` blueprint in `server.py`.
    *   Add hooks in `services/warroom` and `services/event_ingestion`.

## Verification Plan

### Automated Tests
*   **Unit Tests**: Test Gateway locking logic, Tool parameter validation, and Model CRUD.
*   **Integration Tests**: Simulate full flow: Create Warroom -> Init Agent -> Send Message -> Tool Call -> Archive.

### Manual Verification
1.  **Environment Check**: Verify Letta server connection and pgvector extension.
2.  **Chat Flow**:
    *   Create a warroom.
    *   Send a message to `/secops-expert/chat`.
    *   Verify Agent response and L1 state update.
3.  **Tool Execution**:
    *   Ask Agent to query IP reputation (trigger `call_playbook`).
    *   Verify task published to Kafka.
4.  **Streaming**:
    *   Connect to `/secops-expert/chat/stream` and verify SSE output.

## Step-by-Step Implementation

### Phase 1: Infrastructure & Models

- [ ] **Add Dependencies**
    - Update `pyproject.toml` with `letta`, `pgvector`, `httpx`.
    - Run `uv sync`.
    - Verification: `python -c "import letta; import pgvector"`

- [ ] **Create Database Models**
    - Create `models/secops_expert.py` with `AgentMapping`, `ArchivalKnowledge`, `UserPreference`.
    - Register models in `models/__init__.py`.
    - Verification: Check file existence and imports.

- [ ] **Create Migrations**
    - Run `aerich migrate --name add_secops_expert`.
    - Run `aerich upgrade`.
    - Verification: Connect to DB and check tables exist; check `vector` extension.
    - *Note*: Ensure `search_archival_knowledge` function is created by migration.

- [ ] **Configure Settings**
    - Update `settings/__init__.py` and `.env` example.
    - Add Letta, Embedding, and Agent config variables.
    - Verification: `python -c "from settings import RY_AGENT_SERVER; print(RY_AGENT_SERVER)"`

### Phase 2: Core Logic - Gateway & Tools

- [ ] **Implement Embedding Service**
    - Create `libs/embedding.py` to handle OpenAI/Local embedding generation.
    - Verification: Create a test script to generate embedding for "hello".

- [ ] **Implement Tools**
    - Create `services/secops_expert/tools.py`.
    - Implement `call_playbook` (mocking Kafka for now or real integration).
    - Implement `list_available_playbooks`.
    - Implement `submit_case_learning`.
    - Implement `register_tools_to_letta`.
    - Verification: Unit test each function with mock data.

- [ ] **Implement Prompts**
    - Create `services/secops_expert/prompts.py`.
    - Define `SYSTEM_PROMPT_TEMPLATE`, `CORE_MEMORY_SCHEMA`.
    - Implement `build_system_prompt` and `build_members_roster`.
    - Verification: Print generated prompt for inspection.

- [ ] **Implement Gateway Core**
    - Create `services/secops_expert/gateway.py`.
    - Implement `_distributed_lock` with Lua script.
    - Implement `get_or_create_agent` with locking logic.
    - Implement `send_message` and `archive_agent`.
    - Verification: Unit test locking mechanism using mocked Redis.

### Phase 3: API & Lifecycle

- [ ] **Implement Agent Lifecycle Manager**
    - Create `services/secops_expert/agent_manager.py`.
    - Implement `on_warroom_created`, `on_warroom_archived`, `on_event_auto_trigger`.
    - Verification: Mock Gateway and verify manager calls correct methods.

- [ ] **Implement API Views (Part 1: Chat)**
    - Create `services/secops_expert/views.py`.
    - Implement `/chat` and `/state` endpoints.
    - Implement `/chat/stream` SSE endpoint.
    - Verification: `curl` test against endpoints (mocking Gateway).

- [ ] **Implement API Views (Part 2: Management)**
    - Add `/init`, `/archive`, `/knowledge/*`, `/preference/*` endpoints to `views.py`.
    - Verification: `curl` test against endpoints.

- [ ] **Register Blueprint & Initialize**
    - Create `services/secops_expert/__init__.py`.
    - Update `server.py` to register blueprint.
    - Add `setup_secops_expert` listener in `core/listeners/__init__.py`.
    - Verification: Start server and check logs for Gateway initialization.

### Phase 4: Integration Hooks

- [ ] **Integrate with Warroom Service**
    - Update `services/warroom/views.py` to call `AgentLifecycleManager` on create/archive.
    - Verification: Create a warroom via API and check `secops_agent_mapping` table.

- [ ] **Integrate with Event Ingestion (Optional/Later)**
    - Update `services/event_ingestion` for auto-trigger.
    - Verification: Simulate event ingestion.

### Phase 5: Final Polish

- [ ] **Health Check Script**
    - Create `scripts/check_secops_health.py`.
    - Verification: Run script and ensure all checks pass.

- [ ] **Documentation**
    - Update `README.md` or `AGENTS.md` with SecOpsExpert details.
