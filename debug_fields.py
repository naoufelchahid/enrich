import os
import requests
from enrich_companies import load_env

load_env()
api_url = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005/rest")
api_key = os.environ.get("TWENTY_API_KEY")

headers = {"Authorization": f"Bearer {api_key}"}
metadata_url = api_url.replace("/rest", "/metadata")
resp = requests.get(f"{metadata_url}/objects/company/fields", headers=headers)
fields = resp.json().get("data", [])
print(f"Company has {len(fields)} fields")
for f in fields:
    print(f.get("name"), f.get("label"), f.get("isActive"))
