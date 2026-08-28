"""Read-only monthly budget adapter for the prototype's Google Sheet."""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import quote
from urllib.request import urlopen

from .models import BudgetSnapshot


DEFAULT_DEMO_BUDGET_SHEET_ID = "1mGtMi5zzOeC9EoxiF4DBJh_LV2NB1vtJP3Y-4Vo7jq0"


class BudgetProvider(Protocol):
    def monthly_budget(self, *, department: str, month: str) -> BudgetSnapshot: ...


class BudgetSourceError(RuntimeError):
    """Raised when the approved budget source cannot produce a safe answer."""


@dataclass(frozen=True)
class GoogleSheetsBudgetProvider:
    """Read a public demo sheet; production uses a service-account adapter."""

    spreadsheet_id: str = DEFAULT_DEMO_BUDGET_SHEET_ID
    tab_name: str = "Sheet1"
    timeout_seconds: int = 10

    @classmethod
    def from_environment(cls) -> "GoogleSheetsBudgetProvider":
        return cls(
            spreadsheet_id=os.getenv("GOOGLE_BUDGET_SHEET_ID", DEFAULT_DEMO_BUDGET_SHEET_ID),
            tab_name=os.getenv("GOOGLE_BUDGET_SHEET_TAB", "Sheet1"),
        )

    def monthly_budget(self, *, department: str, month: str) -> BudgetSnapshot:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.spreadsheet_id):
            raise BudgetSourceError("Invalid Google budget spreadsheet ID.")
        try:
            url = (
                f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/gviz/tq"
                f"?tqx=out:csv&sheet={quote(self.tab_name)}"
            )
            with urlopen(url, timeout=self.timeout_seconds) as response:  # nosec B310: fixed Google Sheets host
                rows = list(csv.reader(io.TextIOWrapper(response, encoding="utf-8")))
        except OSError as error:
            raise BudgetSourceError("Unable to read the approved Google budget sheet.") from error

        if not rows or len(rows[0]) < 2:
            raise BudgetSourceError("Google budget sheet has no usable monthly budget headers.")
        target_header = datetime.strptime(month, "%Y-%m").strftime("%b %Y")
        try:
            month_index = next(index for index, header in enumerate(rows[0]) if header.strip() == target_header)
        except StopIteration as error:
            raise BudgetSourceError(f"No allocation found for {target_header} in the Google budget sheet.") from error

        normalized_department = department.strip().casefold()
        for row in rows[1:]:
            if row and row[0].strip().casefold() == normalized_department:
                try:
                    allocated = float(row[month_index].replace(",", "").strip())
                except (IndexError, ValueError) as error:
                    raise BudgetSourceError(f"Invalid allocation for {department} in {target_header}.") from error
                return BudgetSnapshot(
                    department=department,
                    month=month,
                    allocated_gel=allocated,
                    committed_gel=0,
                    available_gel=allocated,
                    checked_at=datetime.now(timezone.utc).isoformat(),
                    source=f"google-sheets:{self.spreadsheet_id}:{self.tab_name}",
                )
        raise BudgetSourceError(f"Department '{department}' is not present in the Google budget sheet.")


@dataclass(frozen=True)
class FixedBudgetProvider:
    """Deterministic provider used by automated tests only."""

    available_gel: float

    def monthly_budget(self, *, department: str, month: str) -> BudgetSnapshot:
        return BudgetSnapshot(
            department=department,
            month=month,
            allocated_gel=self.available_gel,
            committed_gel=0,
            available_gel=self.available_gel,
            checked_at=datetime.now(timezone.utc).isoformat(),
            source="test-fixed-budget-provider",
        )
