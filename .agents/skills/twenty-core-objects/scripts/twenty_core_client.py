#!/usr/bin/env python3
"""
Twenty CRM Core Objects & Metadata REST API CLI Helper Script
Provides CLI tools to create, read, update, and delete Core Objects, Fields, Webhooks, API Keys, and Views.
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error
import urllib.parse

DEFAULT_BASE_URL = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005")

def get_headers(token=None):
    if not token:
        token = os.environ.get("TWENTY_API_TOKEN", "")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def make_request(url, method="GET", data=None, token=None):
    headers = get_headers(token)
    encoded_data = None
    if data is not None:
        if isinstance(data, (dict, list)):
            encoded_data = json.dumps(data).encode("utf-8")
        elif isinstance(data, str):
            encoded_data = data.encode("utf-8")

    req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            if body:
                try:
                    return resp.status, json.loads(body)
                except json.JSONDecodeError:
                    return resp.status, body
            return resp.status, None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            parsed_err = json.loads(err_body)
        except Exception:
            parsed_err = err_body
        return e.code, {"error": parsed_err, "http_code": e.code}
    except Exception as e:
        return 500, {"error": str(e)}

def build_url(base_url, endpoint, params=None):
    base_url = base_url.rstrip("/")
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    url = base_url + endpoint
    if params:
        query_str = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if query_str:
            url += "?" + query_str
    return url

def generic_action(args, resource_slug, method, record_id=None, data_payload=None):
    endpoint = f"/rest/metadata/{resource_slug}"
    if record_id:
        endpoint += f"/{record_id}"
    url = build_url(args.base_url, endpoint)
    status, result = make_request(url, method=method, data=data_payload, token=args.token)
    print(json.dumps(result, indent=2))
    if status >= 400:
        sys.exit(1)

def parse_input_data(args):
    if args.data:
        return json.loads(args.data)
    elif hasattr(args, "file") and args.file:
        with open(args.file, "r") as f:
            return json.load(f)
    return None

def main():
    parser = argparse.ArgumentParser(description="Twenty CRM Core Objects & Metadata CLI Client")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base server URL (default: http://localhost:3005)")
    parser.add_argument("--token", help="Bearer API token (overrides TWENTY_API_TOKEN env var)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Resource definitions: (subcommand_prefix, resource_slug)
    resources = [
        ("object", "objects", "Object Metadata"),
        ("field", "fields", "Field Metadata"),
        ("webhook", "webhooks", "Webhooks"),
        ("apikey", "apiKeys", "API Keys"),
        ("view", "views", "Views"),
        ("pagelayout", "pageLayouts", "Page Layouts"),
    ]

    for prefix, slug, name in resources:
        # List
        p_list = subparsers.add_parser(f"list-{prefix}s", help=f"List all {name}")
        p_list.set_defaults(func=lambda a, s=slug: generic_action(a, s, "GET"))

        # Get
        p_get = subparsers.add_parser(f"get-{prefix}", help=f"Get single {name} by ID")
        p_get.add_argument("id", help="Resource UUID")
        p_get.set_defaults(func=lambda a, s=slug: generic_action(a, s, "GET", record_id=a.id))

        # Create
        p_create = subparsers.add_parser(f"create-{prefix}", help=f"Create new {name}")
        p_create.add_argument("--data", help="JSON string data payload")
        p_create.add_argument("--file", help="Path to JSON file with payload")
        p_create.set_defaults(func=lambda a, s=slug: generic_action(a, s, "POST", data_payload=parse_input_data(a)))

        # Update
        p_update = subparsers.add_parser(f"update-{prefix}", help=f"Update existing {name} by ID")
        p_update.add_argument("id", help="Resource UUID")
        p_update.add_argument("--data", help="JSON string data payload")
        p_update.add_argument("--file", help="Path to JSON file with payload")
        p_update.set_defaults(func=lambda a, s=slug: generic_action(a, s, "PATCH", record_id=a.id, data_payload=parse_input_data(a)))

        # Delete
        p_delete = subparsers.add_parser(f"delete-{prefix}", help=f"Delete {name} by ID")
        p_delete.add_argument("id", help="Resource UUID")
        p_delete.set_defaults(func=lambda a, s=slug: generic_action(a, s, "DELETE", record_id=a.id))

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
