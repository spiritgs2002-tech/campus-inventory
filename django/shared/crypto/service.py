from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from django.conf import settings
from shared.errors import BusinessError


def cipher():
    try:
        keys = [Fernet(value.encode()) for value in settings.PII_ENCRYPTION_KEYS]
        if not keys:
            raise ValueError()
        return MultiFernet(keys)
    except (ValueError, TypeError):
        raise BusinessError(
            "ENCRYPTION_NOT_CONFIGURED", "请配置联系信息加密密钥", 503
        ) from None


def encrypt_text(value):
    return "fernet:" + cipher().encrypt(value.encode()).decode() if value else None


def decrypt_text(value):
    if not value:
        return ""
    if not value.startswith("fernet:"):
        raise BusinessError(
            "LEGACY_CONTACT_REQUIRES_MIGRATION", "历史联系信息需受控加密迁移", 409
        )
    try:
        return cipher().decrypt(value[7:].encode()).decode()
    except (InvalidToken, UnicodeError):
        raise BusinessError(
            "CONTACT_DECRYPTION_FAILED", "联系信息无法解密，请核对密钥", 503
        ) from None
