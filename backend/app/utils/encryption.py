from cryptography.fernet import Fernet
import logging
import os
from .secrets_manager import secrets

logger = logging.getLogger(__name__)

class EncryptionManager:
    """Handles encryption and decryption of sensitive data using Fernet symmetric encryption."""
    
    def __init__(self):
        # Get the encryption key from secrets manager
        self.key = secrets.get_secret("ALPR_ENCRYPTION_KEY")
        if not self.key:
            logger.warning("ALPR_ENCRYPTION_KEY not found in secrets. Generating a temporary one for this session.")
            self.key = Fernet.generate_key().decode()
        
        try:
            self.cipher_suite = Fernet(self.key.encode())
        except Exception as e:
            logger.error(f"Failed to initialize Fernet with provided key: {e}")
            # Fallback to a random key to avoid crashing, but log heavily
            self.cipher_suite = Fernet(Fernet.generate_key())

    def encrypt(self, plaintext: str) -> str:
        """Encrypts a string and returns the base64 encoded ciphertext."""
        if not plaintext:
            return plaintext
        try:
            return self.cipher_suite.encrypt(plaintext.encode()).decode()
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            return plaintext

    def decrypt(self, ciphertext: str) -> str:
        """Decrypts a base64 encoded ciphertext and returns the original string."""
        if not ciphertext:
            return ciphertext
        try:
            return self.cipher_suite.decrypt(ciphertext.encode()).decode()
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return ciphertext

# Global instance
encryption_manager = EncryptionManager()
