-- LangGraph PostgresSaver creates checkpoint tables in this non-exposed schema.
-- The setup script must run with LANGGRAPH_CHECKPOINT_SCHEMA=langgraph once
-- after this migration and before the API is deployed.
create schema if not exists langgraph;

revoke all on schema langgraph from public, anon, authenticated;
