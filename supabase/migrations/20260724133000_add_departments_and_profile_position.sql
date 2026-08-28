-- Controlled organisational catalogue. Departments and job positions are
-- assignment data, not user-editable authorization data.
create table public.departments (
  id uuid primary key default extensions.gen_random_uuid(),
  code text not null unique,
  name text not null unique,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger departments_set_updated_at before update on public.departments
for each row execute function public.set_updated_at();

insert into public.departments (code, name) values
  ('internal-audit', 'Internal Audit Department'),
  ('customer-service-quality', 'Customer Service and Quality Management Department'),
  ('security', 'Security Department'),
  ('aml', 'Anti-Money Laundering (AML) Department'),
  ('human-resources', 'Human Resources Department'),
  ('information-security', 'Information Security Department'),
  ('marketing-communications', 'Marketing and Communications Department'),
  ('financial-management', 'Financial Management Department'),
  ('credit', 'Credit Department'),
  ('customer-support', 'Customer Support Department'),
  ('digital-products-alternative-channels-sales', 'Digital Products and Alternative Channels Sales Department'),
  ('product-support', 'Product Support Department'),
  ('products', 'Products Department'),
  ('legal-compliance', 'Legal and Compliance Department'),
  ('software-development', 'Software Development Department'),
  ('risk-management-controlling', 'Risk Management and Controlling Department'),
  ('operations', 'Operations Department'),
  ('administration', 'Administration Department')
on conflict (code) do update
set name = excluded.name, is_active = true;

alter table public.profiles
  add column position text,
  add column department_id uuid references public.departments(id) on delete restrict;

-- The prototype has no existing profiles. Drop the unvalidated free-text
-- field so every future profile references the controlled catalogue.
alter table public.profiles drop column department;
create index profiles_department_idx on public.profiles (department_id)
  where department_id is not null;

alter table public.departments enable row level security;
create policy "departments_select_authenticated" on public.departments
  for select to authenticated using (true);

-- A person must not self-assign a department or position. These fields are
-- assigned by the trusted administration service using the service credential.
drop policy "profiles_update_self" on public.profiles;
