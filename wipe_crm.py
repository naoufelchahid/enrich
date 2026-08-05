import os
from enricher.crm_client import TwentyCRMClient
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("TWENTY_API_KEY")
api_base_url = os.getenv("TWENTY_API_BASE_URL", "http://localhost:3005/rest").rstrip('/')

client = TwentyCRMClient(api_base_url, api_key)

def delete_all(resource: str):
    print(f"Deleting all {resource}...")
    while True:
        resp = client._request_with_retry("GET", f"{api_base_url}/{resource}", headers=client.headers, params={"limit": 100})
        if resp.status_code != 200:
            print(f"Failed to fetch {resource}: {resp.text}")
            break
        
        items = resp.json().get("data", {}).get(resource, [])
        if not items:
            break
        
        for item in items:
            item_id = item["id"]
            del_resp = client._request_with_retry("DELETE", f"{api_base_url}/{resource}/{item_id}", headers=client.headers)
            if del_resp.status_code in (200, 204):
                print(f"Deleted {resource[:-1]} {item_id}")
            else:
                print(f"Failed to delete {resource[:-1]} {item_id}: {del_resp.text}")

if __name__ == "__main__":
    delete_all("people")
    delete_all("companies")
    print("Done wiping data.")
