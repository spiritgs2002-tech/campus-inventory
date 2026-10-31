import bcrypt
from shared.errors import BusinessError

# A valid cost-12 hash used to equalize the missing-account password check.
_DUMMY = bcrypt.hashpw(b"not-a-user-password", bcrypt.gensalt(rounds=12))


def hash_password(value):
    if not isinstance(value, str) or len(value) < 12 or len(value.encode()) > 72:
        raise BusinessError(
            "INVALID_PASSWORD", "密码至少12个字符，UTF-8长度不得超过72字节"
        )
    return bcrypt.hashpw(value.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(value, stored=None):
    try:
        return (
            bcrypt.checkpw(value.encode(), stored.encode() if stored else _DUMMY)
            and stored is not None
        )
    except (ValueError, TypeError, AttributeError):
        return False
