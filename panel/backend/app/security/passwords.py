from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from zxcvbn import zxcvbn

MIN_LENGTH = 12
MIN_SCORE = 3

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_password(password: str, user_inputs: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Returns policy violation codes; an empty list means the password is acceptable."""
    if len(password) < MIN_LENGTH:
        return ["too_short"]
    if zxcvbn(password[:100], user_inputs=list(user_inputs))["score"] < MIN_SCORE:
        return ["too_weak"]
    return []
