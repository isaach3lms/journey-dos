"""What the system is allowed to email people about.

Categories are a Python data structure for the same reason stages and sequences
are: they are shape, not content. Adding one is a constant plus the feature
that sends it, never a migration.

The `is_transactional` flag is the important one and it is not a convenience.
Some mail is not marketing and cannot be opted out of without breaking the
product: a password reset, a check-in confirmation, a receipt. Treating those
as promotional means someone who unsubscribed from the weekly digest can no
longer get back into their own account. Treating promotional mail as
transactional is the opposite failure and is how a church ends up in a spam
complaint. The distinction has to be declared per category, at the point the
category is defined, rather than decided at each send site.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    code: str
    label: str
    # What a person sees on their preferences screen, in their words.
    description: str
    # Transactional mail ignores opt-out. See the module docstring.
    is_transactional: bool
    # Whether a person receives this unless they say otherwise.
    default_on: bool = True


CATEGORIES: tuple[Category, ...] = (
    # Transactional. These send regardless of preference, by design.
    Category(
        "account",
        "Account and security",
        "Password resets and sign-in help. These always send.",
        is_transactional=True,
    ),
    Category(
        "kids_checkin",
        "Kids check-in",
        "Pickup codes and check-in confirmations. These always send.",
        is_transactional=True,
    ),
    Category(
        "giving_receipt",
        "Giving receipts",
        "Receipts and year-end statements. These always send.",
        is_transactional=True,
    ),

    # Everything else is opt-out-able.
    Category(
        "welcome",
        "Welcome and first steps",
        "A short series after your first visit.",
        is_transactional=False,
    ),
    Category(
        "next_step",
        "Next steps",
        "An invitation when there is a next step for you.",
        is_transactional=False,
    ),
    Category(
        "group",
        "Groups and serving",
        "Messages from a group or team you belong to.",
        is_transactional=False,
    ),
    Category(
        "announcement",
        "Church announcements",
        "Church-wide news, sent rarely.",
        is_transactional=False,
    ),
    Category(
        "digest",
        "Weekly digest",
        "A summary of what is coming up.",
        is_transactional=False,
        default_on=False,
    ),
)

CATEGORY_CODES: tuple[str, ...] = tuple(c.code for c in CATEGORIES)
CATEGORY_BY_CODE: dict[str, Category] = {c.code: c for c in CATEGORIES}

TRANSACTIONAL_CODES = frozenset(c.code for c in CATEGORIES if c.is_transactional)
OPTIONAL_CATEGORIES = tuple(c for c in CATEGORIES if not c.is_transactional)


def is_transactional(code: str) -> bool:
    category = CATEGORY_BY_CODE.get(code)
    return bool(category and category.is_transactional)


def category_label(code: str) -> str:
    category = CATEGORY_BY_CODE.get(code)
    return category.label if category else code.replace("_", " ").title()


def default_on(code: str) -> bool:
    category = CATEGORY_BY_CODE.get(code)
    return category.default_on if category else True
