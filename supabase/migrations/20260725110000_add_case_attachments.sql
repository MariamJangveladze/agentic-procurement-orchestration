-- Private case attachments are written by the trusted workflow API only.
-- Browser users can read files only when they can access the parent case.
create type public.case_document_type as enum (
  'request_attachment',
  'supplier_offer',
  'supplier_evidence',
  'tender_document',
  'delivery_document',
  'acceptance_act',
  'other'
);

create table public.case_documents (
  id uuid primary key default extensions.gen_random_uuid(),
  case_id uuid not null references public.procurement_cases(id) on delete cascade,
  document_type public.case_document_type not null,
  file_name text not null,
  content_type text not null,
  byte_size bigint not null check (byte_size > 0 and byte_size <= 52428800),
  storage_path text not null unique,
  uploaded_by uuid references auth.users(id),
  version integer not null default 1 check (version > 0),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index case_documents_case_idx on public.case_documents (case_id, created_at desc);
alter table public.case_documents enable row level security;

create policy "case_documents_select_case_members"
on public.case_documents for select to authenticated
using (private.can_access_case(case_id));

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'case-attachments',
  'case-attachments',
  false,
  52428800,
  array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/msword',
    'image/png',
    'image/jpeg'
  ]
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create policy "case_attachment_files_read_by_case_member"
on storage.objects for select to authenticated
using (
  bucket_id = 'case-attachments'
  and exists (
    select 1 from public.case_documents d
    where d.storage_path = name
      and private.can_access_case(d.case_id)
  )
);
