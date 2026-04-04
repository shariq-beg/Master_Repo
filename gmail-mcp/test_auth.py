from auth import get_credentials

creds = get_credentials()
print("Authentication successful")
print("Token valid:", creds.valid)