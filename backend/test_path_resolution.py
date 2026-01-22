from pathlib import Path
import os

BASE_DIR = Path("/home/user/R1v0.1/backend")
service_account_key_path = "backend/configs/firebase/service-account-key.json"

key_path = Path(service_account_key_path)
if not key_path.is_absolute():
    key_path = BASE_DIR / key_path

print(f"BASE_DIR: {BASE_DIR}")
print(f"service_account_key_path: {service_account_key_path}")
print(f"Resolved key_path: {key_path}")
print(f"Exists: {key_path.exists()}")

# Try without 'backend/' prefix
service_account_key_path_alt = "configs/firebase/service-account-key.json"
key_path_alt = BASE_DIR / service_account_key_path_alt
print(f"Resolved key_path_alt: {key_path_alt}")
print(f"Exists: {key_path_alt.exists()}")
