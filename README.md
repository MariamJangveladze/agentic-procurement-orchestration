# Agentic Procurement Orchestration

Production-minded architecture and runnable demo for an organization's internal procurement process, orchestrated with LangGraph and supervised through explicit human approval gates.

The project demonstrates how a financial organization can combine deterministic policy, agent-assisted evidence preparation, durable workflow state, and accountable human decisions without giving an LLM authority over procurement outcomes.

## What is implemented

- [`docs/STEP_BY_STEP_ARCHITECTURE.md`](docs/STEP_BY_STEP_ARCHITECTURE.md) — proposed prototype architecture, workflow, human-control boundaries, local/cloud deployment, and implementation sequence.
- `src/procurement_demo/` — LangGraph procurement workflow and LangChain agents.
- `app.py` — Streamlit showcase UI with role-specific HITL decisions.
- `run_demo.py` — terminal walkthrough of the full approval flow.
- `supabase/migrations/` — versioned Supabase schema for Auth, cases, HITL actions, audit events and pgvector knowledge.
- `src/procurement_demo/supabase_store.py` — server-only Supabase adapters for case persistence and semantic retrieval.

## Run locally

Requires Python 3.10–3.13 and [uv](https://docs.astral.sh/uv/).

```bash
cd agentic-procurement-orchestration
cp .env.example .env
uv sync --all-groups
uv run streamlit run app.py
```

The demo runs in deterministic mode when `PROCUREMENT_MODEL` is empty. Set a supported LangChain model string and the corresponding provider key in `.env` to activate the real agents; for example:

```dotenv
PROCUREMENT_MODEL=openai:gpt-5.5
OPENAI_API_KEY=...
```

With `OPENAI_API_KEY` configured, the trusted local supplier/policy snippets are also indexed using `text-embedding-3-small` and retrieved semantically. Without it, the demo uses a deterministic local keyword retriever and sends no case data externally.

Run the terminal scenario or tests with:

```bash
uv run python run_demo.py
uv run pytest
```

## Supabase: Auth, workflow management and vector RAG

The connected Supabase project is the durable data layer:

- **Auth and roles:** Supabase Auth creates a `profiles` record and a default requester role. Organization administrators assign Logistics, Finance, Legal, and department-head roles in `user_roles`.
- **Flow management:** `procurement_cases` stores the case projection and latest graph state; `case_actions` holds explicit pending HITL actions; `case_events` is the append-only audit timeline; `case_messages` supports case-specific discussion.
- **LLM knowledge base:** `knowledge_documents` and `knowledge_chunks` store approved source content and 1,536-dimensional pgvector embeddings. This is server-only: browser users have no read access to supplier/policy content.

Run the database migrations against the selected Supabase project before using the server integration. They are already applied to the current `SB_Agentic_Logistics` demo project.

Configure server secrets in `.env`:

```dotenv
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only-secret>
LANGGRAPH_DATABASE_URL=postgresql://postgres.<project-ref>:<database-password>@<supabase-host>:5432/postgres?sslmode=require
OPENAI_API_KEY=<server-only-embedding-and-agent-key>
```

Then index the fictional demo corpus:

```bash
uv run python seed_knowledge.py
```

This command sends those documents to the embedding provider. Use only synthetic or approved material. `SupabaseKnowledgeBase` activates automatically when all server credentials are configured. A trusted API uses `DurableWorkflowService` with `SupabaseCaseStore` to create/sync durable cases on every graph start and resume. The existing Streamlit showcase remains intentionally local because it has no authenticated user identity.

### Durable LangGraph approvals

Set `LANGGRAPH_DATABASE_URL` to the server-only Postgres URL from Supabase
Dashboard → Connect. It stores LangGraph checkpoints, so HITL approvals survive
Vercel cold starts and can resume on another instance. Run this once before the
first deployed API starts:

```bash
uv run python scripts/setup_langgraph_checkpoints.py
```

Do not use the Supabase API URL or a browser publishable key for this variable.

For the Lovable **Request Hub** frontend, configure only these browser-safe values in the Lovable project settings:

```dotenv
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=<publishable-key>
```

Never add `SUPABASE_SERVICE_ROLE_KEY` or `OPENAI_API_KEY` to Lovable. The frontend authenticates users and reads RLS-authorized case data; it sends workflow commands to the trusted LangGraph API, which validates the command, resumes the graph, writes the audit event, and updates the case projection.

## Human control boundary

Agents classify, retrieve supplier evidence, prepare review packs, and draft documents. They cannot approve, reject, choose a supplier, start procurement, accept delivery, or close a case. Every such action is an authenticated human decision that pauses and resumes the LangGraph case thread.

## LangSmith observability

LangSmith support is integrated but **off by default**. To enable it for fictional or approved anonymized test data:

1. Create a LangSmith API key at [smith.langchain.com](https://smith.langchain.com/).
2. Copy `.env.example` to `.env` if you have not already done so.
3. Set:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-key
LANGSMITH_PROJECT=agentic-procurement-orchestration
```

4. Run `uv run python run_demo.py` or `uv run streamlit run app.py`.

Every workflow invocation is tagged `logistics-procurement`, `hitl`, and `local-demo`. Traces include the workflow and agent component name, case ID, workflow version, and model-enabled flag. Do not enable LangSmith Cloud for real organizational data until security, data residency, retention, and redaction requirements are approved.

## Portfolio scope

All sample people, suppliers, requests, and documents are fictional. The repository is an independent portfolio demonstration and is not a representation of any specific organization's production policy or infrastructure.
