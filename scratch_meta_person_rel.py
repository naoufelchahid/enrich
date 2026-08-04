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

meta_resp = requests.get(f"{api_url}/metadata/objects", headers=headers)
data = meta_resp.json()
objects = data.get("data", [])
for obj in objects:
    if obj.get("nameSingular") == "person":
        fields = obj.get("fields", [])
        for f in fields:
            if f.get("name") == "company":
                print(json.dumps(f, indent=2))
