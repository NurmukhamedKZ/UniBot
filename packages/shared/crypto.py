from cryptography.fernet import Fernet

from packages.shared.config import ENCRYPTION_KEY

if not ENCRYPTION_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY not set. Generate one with: "
        "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )

_fernet = Fernet(ENCRYPTION_KEY.encode())


def encrypt_password(password: str) -> str:
    return _fernet.encrypt(password.encode()).decode()


def decrypt_password(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
