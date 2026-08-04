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
  "name": "CAPITAL EVENTS SARL",
  "ice": "001534773000014",
  "rc": "93331 Casablanca",
  "creation": "1998",
  "capital": "S.a.r.l. 2,500,000.00 DH",
  "chiffreDAffaires": "de 50,000,000 à 100,000,000 Dh",
  "effectif": "entre 20 et 50",
  "phone": "+212-522984898",
  "address": {
      "addressStreet1": "280, bd Bir Anzarane - ex Danton 20100 Casablanca - Maroc",
      "addressCity": "Casablanca"
  },
  "activity": "Communication évènementielle"
}
resp = requests.post(f"{api_url}/companies", headers=headers, json=payload)
print(resp.status_code)
print(resp.text)
