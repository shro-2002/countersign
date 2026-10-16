"""Throwaway PayPal sandbox smoke test (Gate G-A). Not production code.

Reads credentials from ../.env, never prints the secret or access token.
Default run is read-only. `--create-order` adds one $1.00 sandbox order (no money moves).
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
TIMEOUT_SECONDS = 20
SANDBOX_HOST = "api-m.sandbox.paypal.com"

# Scope substrings -> capability they unlock (from developer.paypal.com/apps-scopes-and-credentials)
SCOPE_FEATURES = {
    "payments/payment/authcapture": "Orders authorize/capture",
    "payments/refund": "Refunds",
    "payments/payouts": "Payouts",
    "invoicing": "Invoicing",
    "disputes/read-seller": "Disputes (read)",
    "disputes/update-seller": "Disputes (update/accept)",
    "reporting/search/read": "Transaction Search",
    "subscriptions": "Subscriptions",
    "webhooks": "Webhooks",
    "vault": "Vault",
}


def load_env(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"Missing {path}. Copy .env.example to .env and fill it in.")
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def http(method: str, url: str, headers: dict, body: bytes | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return err.code, {"raw": raw[:200].decode(errors="replace")}


def get_token(env: dict) -> dict:
    basic = base64.b64encode(f"{env['PAYPAL_CLIENT_ID']}:{env['PAYPAL_CLIENT_SECRET']}".encode()).decode()
    status, data = http(
        "POST",
        f"{env['PAYPAL_BASE_URL']}/v1/oauth2/token",
        {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        b"grant_type=client_credentials",
    )
    if status != 200:
        sys.exit(f"FAIL token  HTTP {status}  {data.get('error')}: {data.get('error_description')}")
    return data


def report(name: str, status: int, data: dict, ok_detail: str = "") -> None:
    if 200 <= status < 300:
        print(f"PASS {name:<22} HTTP {status}  {ok_detail}")
        return
    issue = (data.get("details") or [{}])[0].get("issue") or data.get("name") or data.get("error")
    print(f"FAIL {name:<22} HTTP {status}  issue={issue}  debug_id={data.get('debug_id')}")


def run_read_checks(base: str, headers: dict) -> None:
    status, data = http("GET", f"{base}/v1/customer/disputes?page_size=1", headers)
    report("disputes list", status, data, f"items={len(data.get('items', []))}")

    status, data = http("GET", f"{base}/v2/invoicing/invoices?page=1&page_size=1&total_required=true", headers)
    report("invoices list", status, data, f"total={data.get('total_items')}")

    end = datetime.now(timezone.utc).replace(microsecond=0)
    query = urllib.parse.urlencode({
        "start_date": (end - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fields": "transaction_info",
        "page_size": 1,
    })
    status, data = http("GET", f"{base}/v1/reporting/transactions?{query}", headers)
    report("transaction search", status, data, f"total={data.get('total_items')}")

    status, data = http("GET", f"{base}/v1/notifications/webhooks", headers)
    report("webhooks list", status, data, f"count={len(data.get('webhooks', []))}")


def create_order(base: str, headers: dict) -> None:
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": "smoke-test", "amount": {"currency_code": "USD", "value": "1.00"}}],
    }
    order_headers = {**headers, "PayPal-Request-Id": str(uuid.uuid4())}
    status, data = http("POST", f"{base}/v2/checkout/orders", order_headers, json.dumps(body).encode())
    approve = next((link["href"] for link in data.get("links", []) if link.get("rel") in ("approve", "payer-action")), None)
    report("create order ($1)", status, data, f"id={data.get('id')} status={data.get('status')}")
    if approve:
        print(f"     approve as SANDBOX buyer: {approve}")


def capture_order(base: str, headers: dict, order_id: str) -> str | None:
    capture_headers = {**headers, "PayPal-Request-Id": f"smoke-capture-{order_id}"}
    status, data = http("POST", f"{base}/v2/checkout/orders/{order_id}/capture", capture_headers, b"{}")
    capture = ((data.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures", [{}])[0]
    report("capture order", status, data, f"capture_id={capture.get('id')} status={capture.get('status')}")
    return capture.get("id")


def partial_refund(base: str, headers: dict, capture_id: str, amount: str) -> None:
    refund_headers = {**headers, "PayPal-Request-Id": f"smoke-refund-{capture_id}-{amount}"}
    body = json.dumps({"amount": {"currency_code": "USD", "value": amount}}).encode()
    status, data = http("POST", f"{base}/v2/payments/captures/{capture_id}/refund", refund_headers, body)
    report(f"refund ${amount}", status, data, f"refund_id={data.get('id')} status={data.get('status')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--create-order", action="store_true", help="also create one $1 sandbox order")
    parser.add_argument("--capture", metavar="ORDER_ID", help="capture a buyer-approved order, then refund $0.25")
    args = parser.parse_args()

    env = load_env(ENV_PATH)
    missing = [k for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_BASE_URL") if not env.get(k)]
    if missing:
        sys.exit(f"Fill these in .env first: {', '.join(missing)}")
    if urllib.parse.urlparse(env["PAYPAL_BASE_URL"]).hostname != SANDBOX_HOST:
        sys.exit(f"Refusing to run: PAYPAL_BASE_URL must be https://{SANDBOX_HOST}")

    token = get_token(env)
    scopes = token.get("scope", "")
    print(f"PASS {'oauth token':<22} expires_in={token.get('expires_in')}s  app_id={token.get('app_id')}")
    for needle, feature in SCOPE_FEATURES.items():
        print(f"     {'yes' if needle in scopes else 'NO ':<3}  {feature}")

    headers = {"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"}
    run_read_checks(env["PAYPAL_BASE_URL"], headers)
    if args.create_order:
        create_order(env["PAYPAL_BASE_URL"], headers)
    if args.capture:
        capture_id = capture_order(env["PAYPAL_BASE_URL"], headers, args.capture)
        if capture_id:
            partial_refund(env["PAYPAL_BASE_URL"], headers, capture_id, "0.25")


if __name__ == "__main__":
    main()
