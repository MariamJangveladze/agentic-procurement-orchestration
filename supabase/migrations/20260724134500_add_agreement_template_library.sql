-- Approved agreement sources are held separately from RAG content. A draft or
-- Legal-review template is never available to the Agreement Drafting Agent.
create type public.template_lifecycle_status as enum (
  'draft', 'legal_review', 'approved', 'retired'
);

create table public.agreement_templates (
  id uuid primary key default extensions.gen_random_uuid(),
  code text not null unique,
  name text not null,
  description text not null,
  required_fields jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.agreement_template_versions (
  id uuid primary key default extensions.gen_random_uuid(),
  template_id uuid not null references public.agreement_templates(id) on delete restrict,
  version text not null,
  status public.template_lifecycle_status not null default 'draft',
  source_text text not null,
  storage_path text,
  source_checksum text not null,
  change_note text,
  submitted_by uuid references auth.users(id),
  reviewed_by uuid references auth.users(id),
  reviewed_at timestamptz,
  effective_from date,
  retired_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (template_id, version),
  check (
    (status = 'approved' and reviewed_by is not null and reviewed_at is not null)
    or status <> 'approved'
  )
);

create unique index agreement_template_one_approved_version_idx
  on public.agreement_template_versions (template_id)
  where status = 'approved';

create trigger agreement_templates_set_updated_at before update on public.agreement_templates
for each row execute function public.set_updated_at();
create trigger agreement_template_versions_set_updated_at before update on public.agreement_template_versions
for each row execute function public.set_updated_at();

-- Private original-file bucket. Legal compares each linked DOCX/PDF with its
-- text source before it can be activated as the official version.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'agreement-templates',
  'agreement-templates',
  false,
  10485760,
  array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword'
  ]
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

alter table public.agreement_templates enable row level security;
alter table public.agreement_template_versions enable row level security;

create policy "agreement_templates_select_legal_logistics" on public.agreement_templates
  for select to authenticated
  using ((select private.has_case_role(array['legal', 'logistics', 'administrator']::public.app_role[])));
create policy "agreement_template_versions_select_legal_logistics" on public.agreement_template_versions
  for select to authenticated
  using ((select private.has_case_role(array['legal', 'logistics', 'administrator']::public.app_role[])));

-- Mutations happen through the trusted administration service. Browser users
-- cannot create, approve, replace, or retire legal templates directly.
create policy "agreement_template_files_read" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'agreement-templates'
    and (select private.has_case_role(array['legal', 'logistics', 'administrator']::public.app_role[]))
  );
create policy "agreement_template_files_insert" on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'agreement-templates'
    and (select private.has_case_role(array['legal', 'administrator']::public.app_role[]))
  );
create policy "agreement_template_files_update" on storage.objects
  for update to authenticated
  using (
    bucket_id = 'agreement-templates'
    and (select private.has_case_role(array['legal', 'administrator']::public.app_role[]))
  )
  with check (
    bucket_id = 'agreement-templates'
    and (select private.has_case_role(array['legal', 'administrator']::public.app_role[]))
  );
create policy "agreement_template_files_delete" on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'agreement-templates'
    and (select private.has_case_role(array['legal', 'administrator']::public.app_role[]))
  );
