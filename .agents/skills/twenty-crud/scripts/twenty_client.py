#!/usr/bin/env python3
"""
Twenty CRM REST API CLI Helper Script
Provides simple CLI access to perform CRUD operations on Twenty CRM.
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error
import urllib.parse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DEFAULT_BASE_URL = os.environ.get("TWENTY_API_BASE_URL", "http://localhost:3005")
if DEFAULT_BASE_URL.endswith("/rest"):
    DEFAULT_BASE_URL = DEFAULT_BASE_URL[:-5]

def get_headers(token=None):
    if not token:
        token = os.environ.get("TWENTY_API_TOKEN") or os.environ.get("TWENTY_API_KEY", "")
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

def cmd_list(args):
    params = {}
    if args.limit:
        params["limit"] = args.limit
    if args.filter:
        params["filter"] = args.filter
    if args.order_by:
        params["order_by"] = args.order_by
    if args.starting_after:
        params["starting_after"] = args.starting_after
    if args.ending_before:
        params["ending_before"] = args.ending_before

    endpoint = f"/rest/{args.object_name}"
    url = build_url(args.base_url, endpoint, params)
    status, result = make_request(url, method="GET", token=args.token)
    print(json.dumps(result, indent=2))
    if status >= 400:
        sys.exit(1)

def cmd_get(args):
    endpoint = f"/rest/{args.object_name}/{args.record_id}"
    url = build_url(args.base_url, endpoint)
    status, result = make_request(url, method="GET", token=args.token)
    print(json.dumps(result, indent=2))
    if status >= 400:
        sys.exit(1)

def cmd_create(args):
    data = None
    if args.data:
        data = json.loads(args.data)
    elif args.file:
        with open(args.file, "r") as f:
            data = json.load(f)
    else:
        print("Error: Either --data or --file must be specified.", file=sys.stderr)
        sys.exit(1)

    endpoint = f"/rest/{args.object_name}"
    url = build_url(args.base_url, endpoint)
    status, result = make_request(url, method="POST", data=data, token=args.token)
    print(json.dumps(result, indent=2))
    if status >= 400:
        sys.exit(1)

def cmd_update(args):
    data = None
    if args.data:
        data = json.loads(args.data)
    elif args.file:
        with open(args.file, "r") as f:
            data = json.load(f)
    else:
        print("Error: Either --data or --file must be specified.", file=sys.stderr)
        sys.exit(1)

    endpoint = f"/rest/{args.object_name}/{args.record_id}"
    url = build_url(args.base_url, endpoint)
    status, result = make_request(url, method="PATCH", data=data, token=args.token)
    print(json.dumps(result, indent=2))
    if status >= 400:
        sys.exit(1)

def cmd_delete(args):
    endpoint = f"/rest/{args.object_name}/{args.record_id}"
    url = build_url(args.base_url, endpoint)
    status, result = make_request(url, method="DELETE", token=args.token)
    print(json.dumps(result, indent=2))
    if status >= 400:
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Twenty CRM REST API CLI Client")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base server URL (default: http://localhost:3005)")
    parser.add_argument("--token", help="Bearer API token (overrides TWENTY_API_TOKEN env var)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # List
    p_list = subparsers.add_parser("list", help="List records of an object")
    p_list.add_argument("object_name", help="CRM Object name (e.g. companies, people, opportunities, tasks, notes)")
    p_list.add_argument("--limit", type=int, help="Page limit (default 60, max 60)")
    p_list.add_argument("--filter", help="Filter parameter (e.g. name[ilike]:\"%%acme%%\")")
    p_list.add_argument("--order-by", help="Order parameter (e.g. createdAt[DescNullsLast])")
    p_list.add_argument("--starting-after", help="Cursor for next page")
    p_list.add_argument("--ending-before", help="Cursor for previous page")
    p_list.set_defaults(func=cmd_list)

    # Get
    p_get = subparsers.add_parser("get", help="Get single record by ID")
    p_get.add_argument("object_name", help="CRM Object name")
    p_get.add_argument("record_id", help="Record UUID")
    p_get.set_defaults(func=cmd_get)

    # Create
    p_create = subparsers.add_parser("create", help="Create new record")
    p_create.add_argument("object_name", help="CRM Object name")
    p_create.add_argument("--data", help="JSON string data payload")
    p_create.add_argument("--file", help="Path to JSON file with payload")
    p_create.set_defaults(func=cmd_create)

    # Update
    p_update = subparsers.add_parser("update", help="Update existing record by ID")
    p_update.add_argument("object_name", help="CRM Object name")
    p_update.add_argument("record_id", help="Record UUID")
    p_update.add_argument("--data", help="JSON string data payload")
    p_update.add_argument("--file", help="Path to JSON file with payload")
    p_update.set_defaults(func=cmd_update)

    # Delete
    p_delete = subparsers.add_parser("delete", help="Delete record by ID")
    p_delete.add_argument("object_name", help="CRM Object name")
    p_delete.add_argument("record_id", help="Record UUID")
    p_delete.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
