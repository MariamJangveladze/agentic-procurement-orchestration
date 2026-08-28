"""Load the two agreement templates from the provided logistics process file.

They are intentionally loaded with ``legal_review`` status. The workflow and
Agreement Drafting Agent must retrieve only versions formally marked approved.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

from procurement_demo.supabase_store import SupabaseSettings


SOURCE_PATH = Path(os.getenv("AGREEMENT_TEMPLATE_SOURCE", "../Logistics >5000.md"))


def extract(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index].strip()


def upsert_template(client, *, code: str, name: str, description: str, required_fields: list[str], text: str) -> None:
    template = client.table("agreement_templates").upsert(
        {
            "code": code,
            "name": name,
            "description": description,
            "required_fields": required_fields,
        },
        on_conflict="code",
    ).execute().data[0]
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
    client.table("agreement_template_versions").upsert(
        {
            "template_id": template["id"],
            "version": "1.0",
            "status": "legal_review",
            "source_text": text,
            "source_checksum": checksum,
            "change_note": "Text imported from Logistics >5000; linked DOCX must be compared and confirmed by Legal.",
        },
        on_conflict="template_id,version",
    ).execute()
    print(f"Loaded {code} v1.0 as legal_review")


def main() -> None:
    load_dotenv()
    settings = SupabaseSettings.from_environment()
    if not settings:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env first.")
    if not SOURCE_PATH.exists():
        raise SystemExit(f"Template source file not found: {SOURCE_PATH.resolve()}")

    source = SOURCE_PATH.read_text(encoding="utf-8")
    client = create_client(settings.url, settings.service_role_key)
    upsert_template(
        client,
        code="new_supplier_service_agreement",
        name="New Supplier Service Agreement",
        description="Service agreement used for a newly selected supplier.",
        required_fields=[
            "agreement_date", "customer_name", "customer_tax_id", "customer_signatory",
            "supplier_name", "supplier_tax_id", "supplier_signatory", "service_description",
            "amount_gel", "vat", "payment_schedule", "supplier_bank_details", "end_date",
        ],
        text=extract(source, "### New Supplier Agreement template:", "### Delivery Agreement template:"),
    )
    upsert_template(
        client,
        code="delivery_acceptance_act",
        name="Delivery and Acceptance Act",
        description="Acceptance act confirming delivery or completion under an existing agreement.",
        required_fields=[
            "act_date", "provider_name", "provider_tax_id", "recipient_name", "recipient_tax_id",
            "agreement_date", "agreement_number", "service_description", "quantity", "amount_ex_vat",
            "vat_amount", "total_amount", "provider_signatory", "recipient_signatory",
        ],
        text=extract(source, "### Delivery Agreement template:", "# Request Registration"),
    )


if __name__ == "__main__":
    main()
