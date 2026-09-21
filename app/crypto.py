"""Encrypting a credential at rest.

One narrow job: a third party's private API key sits in a column, and a
database read must not hand somebody the ability to act as the church against
that provider. Backups, log shipping, a read replica, a support query pasted
into a chat: all of those move rows out of the database and none of them are
attacks.

**Derived from `SECRET_KEY`, not stored beside the data.** Deriving means there
is one secret to manage rather than two, and it is already the one that must be
set for the application to boot at all. The tradeoff is real and worth naming:
rotating `SECRET_KEY` makes every stored credential unreadable. That is
recoverable, because these are third-party keys a church can re-enter, and it
is documented on the model. It would not be an acceptable tradeoff for
something irreplaceable.

Fernet, not a hand-rolled construction. It is authenticated encryption with a
timestamp, so a tampered value fails loudly rather than decrypting to garbage.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class DecryptionFailed(Exception):
    """The value could not be read with this key.

    Usually means SECRET_KEY changed. Never means "the value is wrong", so the
    message says what actually happened rather than guessing.
    """


def _fernet(secret_key: str) -> Fernet:
    if not secret_key:
        raise ValueError("Cannot encrypt without SECRET_KEY.")
    # Fernet wants 32 url-safe base64 bytes. SECRET_KEY is arbitrary text, so
    # it is hashed to the right shape rather than truncated or padded.
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str | None, secret_key: str) -> str | None:
    if plaintext is None or plaintext == "":
        return None
    return _fernet(secret_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None, secret_key: str) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet(secret_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionFailed(
            "This credential cannot be read with the current SECRET_KEY. If "
            "the key was rotated, the church needs to re-enter its provider "
            "keys; nothing else is lost."
        ) from exc


def mask(value: str | None, keep: int = 4) -> str:
    """What a credential looks like on a screen.

    Enough to recognise which key is stored, never enough to use it. Staff
    need to answer "is the right key in here", not read it back.
    """
    if not value:
        return ""
    if len(value) <= keep:
        return "•" * len(value)
    return "•" * (len(value) - keep) + value[-keep:]
