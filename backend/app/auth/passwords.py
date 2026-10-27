"""Argon2id password hashing (SEC-05, SC-01). Unknown accounts are verified against a dummy hash
so the response time does not reveal whether an account exists (USR-04)."""
import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults = RFC 9106 low-memory profile (m=64 MiB, t=3, p=4).
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")
# Bound concurrent hashing memory (4 x 64 MiB).
_slots = asyncio.Semaphore(4)


async def hash_password(password: str) -> str:
    async with _slots:
        return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(stored_hash: str | None, password: str) -> bool:
    async with _slots:
        return await asyncio.to_thread(_verify, stored_hash or _DUMMY_HASH, password, stored_hash is None)


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def _verify(stored_hash: str, password: str, is_dummy: bool) -> bool:
    try:
        ok = _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return ok and not is_dummy
