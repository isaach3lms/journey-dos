"""
Tithely giving import.

Until Tithely issues API keys, giving comes in as a CSV export from
Giving > Transactions > Export. Column names drift between Tithely exports, so
headers are matched loosely rather than by exact string.

Dedupe is on (church_id, external_id). Re-importing an overlapping date range is
safe and expected.
"""

import csv
import io
import re
from datetime import datetime, timezone

HEADER_ALIASES = {
    "external_id": ["transaction id", "transactionid", "id", "gift id", "charge id"],
    "date": ["date", "transaction date", "gift date", "created at", "date of gift"],
    "amount": ["amount", "gift amount", "gross", "gross amount", "total"],
    "fund": ["fund", "funds", "designation", "category"],
    "first_name": ["first name", "first", "givenname"],
    "last_name": ["last name", "last", "surname"],
    "name": ["name", "donor", "donor name", "full name"],
    "email": ["email", "email address", "donor email"],
    "method": ["payment type", "method", "payment method", "type"],
    "recurring": ["recurring", "is recurring", "frequency"],
}

DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%b %d, %Y",
]


def _map_headers(fieldnames):
    """Return {canonical_name: actual_header}."""
    found = {}
    normalized = {(name or "").strip().lower(): name for name in fieldnames or []}
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                found[canonical] = normalized[alias]
                break
    return found


def _money_to_cents(raw) -> int:
    if raw is None:
        return 0
    cleaned = re.sub(r"[^0-9.\-]", "", str(raw))
    if not cleaned or cleaned in ("-", "."):
        return 0
    return int(round(float(cleaned) * 100))


def _parse_date(raw):
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_rows(file_bytes: bytes):
    """Yield normalized gift dicts from a Tithely CSV export."""
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    columns = _map_headers(reader.fieldnames)

    for row in reader:
        def get(key):
            column = columns.get(key)
            return (row.get(column) or "").strip() if column else ""

        amount_cents = _money_to_cents(get("amount"))
        if amount_cents <= 0:
            continue

        name = get("name")
        if not name:
            name = f"{get('first_name')} {get('last_name')}".strip()

        recurring_raw = get("recurring").lower()
        yield {
            "external_id": get("external_id") or None,
            "given_at": _parse_date(get("date")),
            "amount_cents": amount_cents,
            "fund": get("fund") or "General",
            "donor_name": name or "Unnamed",
            "donor_email": get("email").lower() or None,
            "method": get("method") or "online",
            "is_recurring": recurring_raw in ("yes", "true", "1", "recurring", "monthly", "weekly"),
        }


def import_giving_csv(file_bytes: bytes, church_id: int):
    """Insert gifts, match to people by email, skip anything already imported."""
    from .extensions import db, utcnow
    from .models import GivingRecord, Person

    people_by_email = {
        person.email: person.id
        for person in Person.query.filter_by(church_id=church_id).all()
        if person.email
    }
    existing_ids = {
        record.external_id
        for record in GivingRecord.query.filter_by(church_id=church_id).all()
        if record.external_id
    }

    added = 0
    skipped = 0
    unmatched = 0

    for gift in parse_rows(file_bytes):
        if gift["external_id"] and gift["external_id"] in existing_ids:
            skipped += 1
            continue

        person_id = people_by_email.get(gift["donor_email"]) if gift["donor_email"] else None
        if person_id is None:
            unmatched += 1

        db.session.add(
            GivingRecord(
                church_id=church_id,
                person_id=person_id,
                external_id=gift["external_id"],
                donor_name=gift["donor_name"],
                donor_email=gift["donor_email"],
                amount_cents=gift["amount_cents"],
                fund=gift["fund"],
                method=gift["method"],
                is_recurring=gift["is_recurring"],
                given_at=gift["given_at"] or utcnow(),
            )
        )
        if gift["external_id"]:
            existing_ids.add(gift["external_id"])
        added += 1

    db.session.commit()
    return {"added": added, "skipped": skipped, "unmatched": unmatched}
