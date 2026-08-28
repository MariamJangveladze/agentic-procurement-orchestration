-- The requester does not provide an amount. Logistics supplies the estimate
-- after Department Head approval, so the initial persisted value must be null.
alter table public.procurement_cases
  alter column estimated_amount_gel drop not null;

-- Approval identities are separate from a free-text profile.position. These
-- roles are granted only by an administrator through the controlled role table.
alter type public.app_role add value if not exists 'head_of_logistics';
alter type public.app_role add value if not exists 'director_of_logistics';
alter type public.app_role add value if not exists 'ceo';
