-- Agentic Logistics Core: Supabase Auth, workflow persistence, audit data and RAG.
-- The application client receives only the publishable key. Agent/workflow writes
-- and semantic retrieval are performed by the server-side LangGraph service.

create extension if not exists pgcrypto with schema extensions;
create extension if not exists vector with schema extensions;

create type public.app_role as enum (
  'requester', 'department_head', 'logistics', 'finance', 'legal', 'administrator'
);

create type public.case_action_status as enum ('pending', 'completed', 'cancelled', 'expired');

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  department text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.user_roles (
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.app_role not null,
  granted_at timestamptz not null default now(),
  primary key (user_id, role)
);

create table public.procurement_cases (
  id uuid primary key default extensions.gen_random_uuid(),
  case_number text not null unique,
  requester_id uuid not null references auth.users(id),
  requester_department text not null,
  category text not null check (category in ('material', 'service')),
  subcategory text not null,
  description text not null,
  deadline date,
  estimated_amount_gel numeric(14,2) not null check (estimated_amount_gel > 0),
  status text not null,
  graph_thread_id text not null unique,
  graph_state jsonb not null default '{}'::jsonb,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  closed_at timestamptz
);

create index procurement_cases_requester_idx on public.procurement_cases (requester_id, created_at desc);
create index procurement_cases_status_idx on public.procurement_cases (status, updated_at desc);

create table public.case_members (
  case_id uuid not null references public.procurement_cases(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role public.app_role not null,
  added_at timestamptz not null default now(),
  primary key (case_id, user_id, role)
);

create index case_members_user_idx on public.case_members (user_id, case_id);

-- Append-only event log. The service records every state transition and agent
-- action here; regular users cannot update or delete events.
create table public.case_events (
  id uuid primary key default extensions.gen_random_uuid(),
  case_id uuid not null references public.procurement_cases(id) on delete cascade,
  event_sequence bigint generated always as identity,
  event_type text not null,
  actor_id uuid references auth.users(id),
  actor_label text not null,
  actor_kind text not null check (actor_kind in ('human', 'agent', 'system', 'integration')),
  payload jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now(),
  unique (case_id, event_sequence)
);

create index case_events_case_idx on public.case_events (case_id, event_sequence);

-- A HITL card is an explicit action request, never an implicit chat approval.
create table public.case_actions (
  id uuid primary key default extensions.gen_random_uuid(),
  case_id uuid not null references public.procurement_cases(id) on delete cascade,
  action_kind text not null,
  required_roles public.app_role[] not null,
  status public.case_action_status not null default 'pending',
  action_payload jsonb not null default '{}'::jsonb,
  submitted_by uuid references auth.users(id),
  submitted_payload jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  expires_at timestamptz
);

create index case_actions_pending_idx on public.case_actions (status, created_at)
  where status = 'pending';

create table public.case_messages (
  id uuid primary key default extensions.gen_random_uuid(),
  case_id uuid not null references public.procurement_cases(id) on delete cascade,
  author_id uuid references auth.users(id),
  author_kind text not null check (author_kind in ('human', 'agent', 'system')),
  body text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index case_messages_case_idx on public.case_messages (case_id, created_at);

create table public.knowledge_documents (
  id uuid primary key default extensions.gen_random_uuid(),
  source_key text not null unique,
  title text not null,
  document_type text not null,
  classification text not null default 'internal',
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.knowledge_chunks (
  id uuid primary key default extensions.gen_random_uuid(),
  document_id uuid not null references public.knowledge_documents(id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding extensions.vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (document_id, chunk_index)
);

create index knowledge_chunks_document_idx on public.knowledge_chunks (document_id, chunk_index);
create index knowledge_chunks_embedding_idx on public.knowledge_chunks
  using hnsw (embedding extensions.vector_cosine_ops)
  where embedding is not null;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_set_updated_at before update on public.profiles
for each row execute function public.set_updated_at();
create trigger procurement_cases_set_updated_at before update on public.procurement_cases
for each row execute function public.set_updated_at();
create trigger knowledge_documents_set_updated_at before update on public.knowledge_documents
for each row execute function public.set_updated_at();
create trigger knowledge_chunks_set_updated_at before update on public.knowledge_chunks
for each row execute function public.set_updated_at();

-- Supabase Auth user creation is mirrored into an application profile. This
-- function is deliberately private and not executable by browser roles.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', new.email));
  insert into public.user_roles (user_id, role) values (new.id, 'requester');
  return new;
end;
$$;

revoke all on function public.handle_new_user() from public;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- These policy helpers must look up membership without re-entering the policy
-- of the table being protected. Their inputs are fixed UUID/role values, they
-- use auth.uid(), and execution is granted only to authenticated users.
create or replace function public.has_case_role(required_roles public.app_role[])
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.user_roles
    where user_id = auth.uid() and role = any(required_roles)
  );
$$;

create or replace function public.can_access_case(target_case_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.procurement_cases c
    where c.id = target_case_id and c.requester_id = auth.uid()
  ) or exists (
    select 1 from public.case_members m
    where m.case_id = target_case_id and m.user_id = auth.uid()
  ) or public.has_case_role(array['administrator']::public.app_role[]);
$$;

revoke all on function public.has_case_role(public.app_role[]) from public;
revoke all on function public.can_access_case(uuid) from public;
grant execute on function public.has_case_role(public.app_role[]) to authenticated;
grant execute on function public.can_access_case(uuid) to authenticated;

alter table public.profiles enable row level security;
alter table public.user_roles enable row level security;
alter table public.procurement_cases enable row level security;
alter table public.case_members enable row level security;
alter table public.case_events enable row level security;
alter table public.case_actions enable row level security;
alter table public.case_messages enable row level security;
alter table public.knowledge_documents enable row level security;
alter table public.knowledge_chunks enable row level security;

create policy "profiles_select_self_or_admin" on public.profiles for select
  to authenticated using (id = auth.uid() or public.has_case_role(array['administrator']::public.app_role[]));
create policy "profiles_update_self" on public.profiles for update
  to authenticated using (id = auth.uid()) with check (id = auth.uid());

create policy "roles_select_self" on public.user_roles for select
  to authenticated using (user_id = auth.uid());

create policy "cases_select_members" on public.procurement_cases for select
  to authenticated using (public.can_access_case(id));
create policy "cases_insert_requester" on public.procurement_cases for insert
  to authenticated with check (requester_id = auth.uid());

create policy "members_select_case_members" on public.case_members for select
  to authenticated using (user_id = auth.uid() or public.can_access_case(case_id));

create policy "events_select_case_members" on public.case_events for select
  to authenticated using (public.can_access_case(case_id));

create policy "actions_select_case_members" on public.case_actions for select
  to authenticated using (public.can_access_case(case_id));
create policy "actions_submit_assigned_role" on public.case_actions for update
  to authenticated
  using (status = 'pending' and public.can_access_case(case_id) and public.has_case_role(required_roles))
  with check (submitted_by = auth.uid());

create policy "messages_select_case_members" on public.case_messages for select
  to authenticated using (public.can_access_case(case_id));
create policy "messages_insert_case_members" on public.case_messages for insert
  to authenticated with check (
    author_id = auth.uid() and author_kind = 'human' and public.can_access_case(case_id)
  );

-- Knowledge is server-only in this prototype. The LangGraph service uses its
-- service credential; no browser policy exposes policy or supplier content.

create or replace function public.match_knowledge_chunks(
  query_embedding extensions.vector(1536),
  match_count integer default 4,
  document_types text[] default null
)
returns table (
  chunk_id uuid,
  document_id uuid,
  source_key text,
  title text,
  document_type text,
  content text,
  metadata jsonb,
  similarity double precision
)
language sql
stable
security invoker
set search_path = ''
as $$
  select
    c.id,
    d.id,
    d.source_key,
    d.title,
    d.document_type,
    c.content,
    c.metadata,
    1 - (c.embedding OPERATOR(extensions.<=>) query_embedding) as similarity
  from public.knowledge_chunks c
  join public.knowledge_documents d on d.id = c.document_id
  where c.embedding is not null
    and (document_types is null or d.document_type = any(document_types))
  order by c.embedding OPERATOR(extensions.<=>) query_embedding
  limit greatest(1, least(match_count, 20));
$$;

revoke all on function public.match_knowledge_chunks(extensions.vector, integer, text[]) from public;
grant execute on function public.match_knowledge_chunks(extensions.vector, integer, text[]) to service_role;
