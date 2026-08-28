-- Keep policy helpers out of Supabase's exposed public API. They remain usable
-- by RLS evaluation but cannot be called as REST RPC endpoints.
create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to authenticated;

alter function public.handle_new_user() set schema private;
alter function public.has_case_role(public.app_role[]) set schema private;
alter function public.can_access_case(uuid) set schema private;

revoke all on function private.handle_new_user() from public, anon, authenticated;
revoke all on function private.has_case_role(public.app_role[]) from public, anon;
revoke all on function private.can_access_case(uuid) from public, anon;
grant execute on function private.has_case_role(public.app_role[]) to authenticated;
grant execute on function private.can_access_case(uuid) to authenticated;

alter policy "profiles_select_self_or_admin" on public.profiles
  using (id = (select auth.uid()) or private.has_case_role(array['administrator']::public.app_role[]));
alter policy "profiles_update_self" on public.profiles
  using (id = (select auth.uid())) with check (id = (select auth.uid()));
alter policy "roles_select_self" on public.user_roles
  using (user_id = (select auth.uid()));
alter policy "cases_select_members" on public.procurement_cases
  using (private.can_access_case(id));
alter policy "cases_insert_requester" on public.procurement_cases
  with check (requester_id = (select auth.uid()));
alter policy "members_select_case_members" on public.case_members
  using (user_id = (select auth.uid()) or private.can_access_case(case_id));
alter policy "events_select_case_members" on public.case_events
  using (private.can_access_case(case_id));
alter policy "actions_select_case_members" on public.case_actions
  using (private.can_access_case(case_id));
alter policy "actions_submit_assigned_role" on public.case_actions
  using (status = 'pending' and private.can_access_case(case_id) and private.has_case_role(required_roles))
  with check (submitted_by = (select auth.uid()));
alter policy "messages_select_case_members" on public.case_messages
  using (private.can_access_case(case_id));
alter policy "messages_insert_case_members" on public.case_messages
  with check (author_id = (select auth.uid()) and author_kind = 'human' and private.can_access_case(case_id));

-- Explicit deny policies make the server-only RAG boundary clear to the
-- database linter and prevent future browser access by omission.
create policy "knowledge_documents_server_only" on public.knowledge_documents
  for all to authenticated using (false) with check (false);
create policy "knowledge_chunks_server_only" on public.knowledge_chunks
  for all to authenticated using (false) with check (false);

create index case_actions_case_idx on public.case_actions (case_id, created_at desc);
create index case_actions_submitted_by_idx on public.case_actions (submitted_by) where submitted_by is not null;
create index case_events_actor_idx on public.case_events (actor_id) where actor_id is not null;
create index case_messages_author_idx on public.case_messages (author_id) where author_id is not null;
create index knowledge_documents_created_by_idx on public.knowledge_documents (created_by) where created_by is not null;
