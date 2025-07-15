import firebase_admin
from firebase_admin import auth
from firebase_admin import credentials
import os

# --- IMPORTANT: Initialize Firebase Admin SDK ---
# Replace "path/to/your/serviceAccountKey.json" with the actual path to your Firebase service account key file.
# This file contains the credentials needed to authenticate with your Firebase project.
# Ensure this file is kept secure and not exposed publicly.
service_account_key_path = "C:/Users/HP/Desktop/R1v0.1/backend/configs/firebase/service-account-key.json"

# Check if the default app is already initialized (useful if running within a larger application)
if not firebase_admin._DEFAULT_APP_NAME in firebase_admin._apps:
    try:
        cred = credentials.Certificate(service_account_key_path)
        firebase_admin.initialize_app(cred)
        print("Firebase Admin SDK initialized successfully.")
    except FileNotFoundError:
        print(f"Error: Service account key file not found at {service_account_key_path}")
        print("Please update the 'service_account_key_path' variable with the correct path.")
        exit()
    except Exception as e:
        print(f"Error initializing Firebase Admin SDK: {e}")
        exit()
else:
    print("Firebase Admin SDK already initialized.")

# ------------------------------------------------

user_uid = "lrv2tpm9ICWY139aSGjXbaPVijE2"

try:
    user = auth.get_user(user_uid)
    print(f"User Account Status: {'Disabled' if user.disabled else 'Enabled'}")
    print(f"\nSuccessfully fetched user: {user.uid}")
    print("Custom Claims:")
    if user.custom_claims:
        for key, value in user.custom_claims.items():
            print(f"  {key}: {value}")
    else:
        print("  No custom claims set for this user.")

except firebase_admin.auth.UserNotFoundError:
    print(f"User with UID {user_uid} not found.")
except Exception as e:
    print(f"An error occurred: {e}")