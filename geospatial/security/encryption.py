import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = (12)  # 96 bits standard for GCM
default_version =("1.0.0")

class EncryptionService:
    def __init__(self, key_b64: str, version: str = default_version) -> None:
        if not key_b64:
            raise ValueError("Encryption key must be provided")
        self._key = base64.b64decode(key_b64)
        if len(self._key) != 32:
            raise ValueError("Encryption key must be 32 bytes for AES-256")
        self.__version__bytes = version.encode("utf-8")
        self._aesgcm = AESGCM(self._key)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise ValueError("Plaintext must be provided for encryption")
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), self.__version__bytes)
        prefixed_ciphertext = self.__version__bytes + nonce + ciphertext
        return base64.b64encode(prefixed_ciphertext).decode("utf-8")

    def decrypt(self, ciphertext_b64: str) -> str:
        if not ciphertext_b64:
            raise ValueError("Ciphertext must be provided for decryption")
        ciphertext_bytes = base64.b64decode(ciphertext_b64, validate=True)
        version_length = len(self.__version__bytes)
        if len(ciphertext_bytes) < version_length + NONCE_SIZE:
            raise ValueError("Ciphertext is too short to contain a valid version and nonce")
        if ciphertext_bytes[:version_length] != self.__version__bytes:
            raise ValueError("Incompatible ciphertext version")
        nonce = ciphertext_bytes[version_length:version_length + NONCE_SIZE]
        ciphertext = ciphertext_bytes[version_length + NONCE_SIZE:]
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, self.__version__bytes)
        return plaintext.decode("utf-8")

def get_encryption_service() -> EncryptionService:
    key_b64 = os.environ.get("GEO_CLOUD_ENCRYPTION_KEY", "")
    version = os.environ.get("GEO_CLOUD_ENCRYPTION_VERSION", default_version)
    return EncryptionService(key_b64=key_b64, version=version)
