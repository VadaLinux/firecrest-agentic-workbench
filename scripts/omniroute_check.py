#!/usr/bin/env python3
"""Pre-flight check for OmniRoute proxy model discovery.
"""
import sys
import json
import os
import urllib.request
from urllib.error import URLError, HTTPError
import urllib.parse

def run_check():
    discovery_url = os.environ.get("OMNIROUTE_DISCOVERY_URL", "")
    target_model = os.environ.get("OMNIROUTE_MODEL", "")

    if not discovery_url:
        print("ERROR: OMNIROUTE_DISCOVERY_URL is not set or empty.", file=sys.stderr)
        sys.exit(1)
    if not target_model:
        print("ERROR: OMNIROUTE_MODEL is not set or empty.", file=sys.stderr)
        sys.exit(1)

    # Normalize url adding /v1 if missing
    parsed = urllib.parse.urlparse(discovery_url)
    path = parsed.path.rstrip('/')
    if not path.endswith('/v1'):
        path += '/v1'
    base_endpoint = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )
    models_url = base_endpoint + "/models"

    req = urllib.request.Request(models_url)
    api_key = os.environ.get("OMNIROUTE_API_KEY", "").strip()
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=3.0) as response:
            body = response.read()
            # If we don't get 2xx, urlopen raises Exception, but let's be safe
            if response.status >= 300:
                print(f"ERROR: /v1/models returned HTTP {response.status}", file=sys.stderr)
                sys.exit(1)
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                print("ERROR: /v1/models response is not valid JSON.", file=sys.stderr)
                sys.exit(1)

            if "data" not in data or not isinstance(data["data"], list):
                print("ERROR: /v1/models response JSON is malformed (missing 'data' list).", file=sys.stderr)
                sys.exit(1)

            model_ids = {m.get("id") for m in data["data"] if isinstance(m, dict)}
            if target_model not in model_ids:
                print(f"ERROR: Model '{target_model}' not found in /v1/models discovery.", file=sys.stderr)
                sys.exit(1)

            print(f"OK: Model '{target_model}' successfully discovered.")

    except HTTPError as e:
         print(f"ERROR: Discovery failed with HTTP {e.code}", file=sys.stderr)
         sys.exit(1)
    except URLError as e:
         print(f"ERROR: Discovery unreachable or timed out: {e.reason}", file=sys.stderr)
         sys.exit(1)

    # Capability ping (optional)
    status_url = base_endpoint + "/omniroute/status"
    req_cap = urllib.request.Request(status_url)
    if api_key:
        req_cap.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req_cap, timeout=3.0):
             pass
    except (HTTPError, URLError, TimeoutError, Exception):
        print("unsupported/degraded")
        # Do not exit 1!

if __name__ == '__main__':
    run_check()
