"""Member PINs are independent of long-lived web and merchant credentials."""

import re

from passlib.hash import argon2


PIN = re.compile(r'[0-9]{4,8}')


def is_pin_hash(value):
    if not isinstance(value, str) or not argon2.identify(value):
        return False
    try:
        return bool(argon2.from_string(value).salt)
    except (ValueError, TypeError):
        return False


def hash_pin(value):
    if not isinstance(value, str) or not PIN.fullmatch(value):
        raise ValueError('Invalid member PIN')
    return argon2.hash(value)


def verify_pin(value, digest):
    if not isinstance(value, str) or not PIN.fullmatch(value) or not is_pin_hash(digest):
        return False
    try:
        return argon2.verify(value, digest)
    except (TypeError, ValueError):
        return False
