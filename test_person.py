import os
import requests
import json

env_file = ".env"
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v

api_url = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005/rest")
api_key = os.environ.get("TWENTY_API_KEY")

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

payload = {
    "name": {
        "firstName": "Test",
        "lastName": "User"
    },
    "emails": {
        "primaryEmail": "test@example.com"
    },
    "phones": {
        "primaryPhoneNumber": "+212600000000"
    }
}

resp = requests.post(f"{api_url}/people", headers=headers, json=payload)
print(resp.status_code)
print(resp.text)
