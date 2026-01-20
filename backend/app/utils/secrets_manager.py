import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class SecretsManager:
    def __init__(self):
        self._provider = os.getenv("SECRETS_PROVIDER", "env").lower()  # env, aws, vault
        logger.info(f"SecretsManager initialized with provider: {self._provider}")

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get secret from configured provider."""
        try:
            if self._provider == "env":
                return os.getenv(key, default)
            
            elif self._provider == "aws":
                try:
                    import boto3
                    from botocore.exceptions import ClientError
                except ImportError:
                    logger.warning("boto3 not installed. Falling back to env for secrets.")
                    return os.getenv(key, default)

                # Assuming the key itself is the secret name in AWS Secrets Manager
                # Or we could have a single secret JSON and key is a key within it.
                # For simplicity, following the prompt's style (key = SecretId)
                try:
                    client = boto3.client('secretsmanager')
                    response = client.get_secret_value(SecretId=key)
                    if 'SecretString' in response:
                        return response['SecretString']
                    return default
                except Exception as e:
                    logger.error(f"Failed to fetch secret '{key}' from AWS: {e}")
                    return default

            elif self._provider == "vault":
                # Placeholder for HashiCorp Vault
                logger.warning("Vault provider not fully implemented. Falling back to env.")
                return os.getenv(key, default)
            
            else:
                return os.getenv(key, default)
                
        except Exception as e:
            logger.error(f"Error retrieving secret '{key}': {e}")
            return default

# Global instance
secrets = SecretsManager()
