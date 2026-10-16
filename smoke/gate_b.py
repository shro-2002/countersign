"""Throwaway Gate G-B probes: invoicing, payouts, disputes, card-funded capture. Not production code.

Every call goes to the PayPal SANDBOX host only (checked before each request).
Amounts are simulated sandbox money. Raw responses are appended to results/gate_b.jsonl.
"""

import argparse
import base64
import json
import time
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import paypal_smoke as smoke

RESULTS = Path(__file__).resolve().parent / "results" / "gate_b.jsonl"
SANDBOX_BASE = f"https://{smoke.SANDBOX_HOST}"
# Published PayPal sandbox test card (developer.paypal.com/sandbox-testing/card-testing)
TEST_CARD = {"name": "Synthetic Tester", "number": "4012888888881881", "expiry": "2030-12", "security_code": "123"}


class Client:
    def __init__(self) -> None:
        self.env = smoke.load_env(smoke.ENV_PATH)
        if self.env.get("PAYPAL_BASE_URL") != SANDBOX_BASE:
            raise SystemExit(f"Refusing: PAYPAL_BASE_URL must be {SANDBOX_BASE}")
        token = smoke.get_token(self.env)
        self.headers = {"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"}

    def call(self, step: str, method: str, path: str, body=None, extra_headers=None, raw_body: bytes | None = None):
        url = SANDBOX_BASE + path
        assert urllib.parse.urlparse(url).hostname == smoke.SANDBOX_HOST, "non-sandbox host blocked"
        headers = {**self.headers, **(extra_headers or {})}
        payload = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        status, data = smoke.http(method, url, headers, payload)
        log(step, method, path, status, data)
        return status, data


def log(step: str, method: str, path: str, status: int, data: dict) -> None:
    RESULTS.parent.mkdir(exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "step": step, "method": method,
             "path": path, "status": status, "response": data}
    with RESULTS.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def summary(step: str, status: int, data: dict, fields: dict) -> None:
    detail = " ".join(f"{k}={v}" for k, v in fields.items())
    if 200 <= status < 300:
        print(f"PASS {step:<30} HTTP {status}  {detail}")
    else:
        issue = (data.get("details") or [{}])[0].get("issue") or data.get("name") or data.get("message")
        print(f"FAIL {step:<30} HTTP {status}  issue={issue} debug_id={data.get('debug_id')}")


def buyer_identity(client: Client, order_id: str) -> tuple[str, str]:
    _, order = client.call("buyer lookup", "GET", f"/v2/checkout/orders/{order_id}")
    payer = order.get("payer", {})
    return payer.get("email_address", ""), payer.get("payer_id", "")


def probe_invoice(client: Client, buyer_email: str) -> None:
    prefer = {"Prefer": "return=representation"}
    body = {
        "detail": {"currency_code": "USD", "note": "Synthetic Gate G-B test invoice (sandbox)."},
        "primary_recipients": [{"billing_info": {"email_address": buyer_email}}],
        "items": [{"name": "Synthetic test item", "quantity": "1", "unit_amount": {"currency_code": "USD", "value": "1.00"}}],
    }
    status, inv = client.call("invoice create", "POST", "/v2/invoicing/invoices", body, prefer)
    invoice_id = inv.get("id") or inv.get("href", "").rsplit("/", 1)[-1]
    summary("invoice create", status, inv, {"id": invoice_id, "status": inv.get("status")})
    if not invoice_id:
        return

    status, data = client.call("invoice send", "POST", f"/v2/invoicing/invoices/{invoice_id}/send",
                               {"send_to_invoicer": False, "send_to_recipient": True})
    summary("invoice send", status, data, {})
    status, inv = client.call("invoice get after send", "GET", f"/v2/invoicing/invoices/{invoice_id}")
    summary("invoice status after send", status, inv, {"status": inv.get("status")})

    external = {"method": "CASH", "payment_date": date.today().isoformat(),
                "amount": {"currency_code": "USD", "value": "1.00"},
                "note": "SYNTHETIC: external cash payment recorded with no PayPal transaction"}
    status, data = client.call("invoice record external payment", "POST",
                               f"/v2/invoicing/invoices/{invoice_id}/payments", external)
    summary("record external payment", status, data, {"payment_id": data.get("payment_id")})

    status, inv = client.call("invoice get after record", "GET", f"/v2/invoicing/invoices/{invoice_id}")
    txns = (inv.get("payments") or {}).get("transactions", [])
    summary("invoice status after record", status, inv, {
        "status": inv.get("status"),
        "paid_amount": (inv.get("payments") or {}).get("paid_amount", {}).get("value"),
        "txn_types": [t.get("type") for t in txns],
        "txn_methods": [t.get("method") for t in txns],
    })


def probe_payout(client: Client, buyer_email: str) -> None:
    batch_id = f"gateb-{uuid.uuid4().hex[:12]}"
    body = {
        "sender_batch_header": {"sender_batch_id": batch_id, "email_subject": "Synthetic sandbox payout"},
        "items": [{"recipient_type": "EMAIL", "amount": {"value": "0.10", "currency": "USD"},
                   "receiver": buyer_email, "note": "Synthetic Gate G-B payout", "sender_item_id": "item-1"}],
    }
    status, data = client.call("payout create", "POST", "/v1/payments/payouts", body,
                               {"PayPal-Request-Id": batch_id})
    header = data.get("batch_header", {})
    summary("payout create", status, data, {"batch_id": header.get("payout_batch_id"),
                                            "batch_status": header.get("batch_status")})
    if not header.get("payout_batch_id"):
        return
    time.sleep(8)
    status, data = client.call("payout get", "GET", f"/v1/payments/payouts/{header['payout_batch_id']}")
    items = data.get("items", [])
    summary("payout status", status, data, {
        "batch_status": data.get("batch_header", {}).get("batch_status"),
        "item_status": [i.get("transaction_status") for i in items],
    })


def unsigned_assertion(client_id: str, payer_id: str) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'iss': client_id, 'payer_id': payer_id})}."


def probe_dispute(client: Client, capture_id: str, payer_id: str) -> None:
    """Time-boxed: two attempts only."""
    boundary = uuid.uuid4().hex
    dispute_input = {
        "disputed_transactions": [{"buyer_transaction_id": capture_id}],
        "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
        "dispute_amount": {"currency_code": "USD", "value": "0.50"},
        "extensions": {"merchant_contacted": True, "merchant_contacted_outcome": "NO_RESPONSE",
                       "merchandize_dispute_properties": {"issue_type": "PRODUCT"}},
    }
    raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"input\"\r\n"
           f"Content-Type: application/json\r\n\r\n{json.dumps(dispute_input)}\r\n--{boundary}--\r\n").encode()
    multipart = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    status, data = client.call("dispute create (no assertion)", "POST", "/v1/customer/disputes",
                               extra_headers=multipart, raw_body=raw)
    summary("dispute create (no assertion)", status, data, {"links": len(data.get("links", []))})

    assertion = unsigned_assertion(client.env["PAYPAL_CLIENT_ID"], payer_id)
    status, data = client.call("dispute create (buyer assertion)", "POST", "/v1/customer/disputes",
                               extra_headers={**multipart, "PayPal-Auth-Assertion": assertion}, raw_body=raw)
    summary("dispute create (buyer assertion)", status, data,
            {"href": (data.get("links") or [{}])[0].get("href")})


def probe_card_capture(client: Client) -> None:
    request_id = f"gateb-card-{uuid.uuid4().hex[:12]}"
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": request_id, "amount": {"currency_code": "USD", "value": "1.00"}}],
        "payment_source": {"card": TEST_CARD},
    }
    status, data = client.call("card order create", "POST", "/v2/checkout/orders", body,
                               {"PayPal-Request-Id": request_id, "Prefer": "return=representation"})
    captures = ((data.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures", [])
    summary("card-funded order", status, data, {
        "order_status": data.get("status"),
        "capture_id": captures[0].get("id") if captures else None,
        "capture_status": captures[0].get("status") if captures else None,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", choices=["invoice", "payout", "dispute", "card", "all"])
    parser.add_argument("--order", default="24J544716M9694218", help="approved order used to find the buyer")
    parser.add_argument("--capture", default="11V17987HD904421G")
    args = parser.parse_args()

    client = Client()
    buyer_email, payer_id = buyer_identity(client, args.order)
    if args.probe in ("invoice", "all"):
        probe_invoice(client, buyer_email)
    if args.probe in ("payout", "all"):
        probe_payout(client, buyer_email)
    if args.probe in ("dispute", "all"):
        probe_dispute(client, args.capture, payer_id)
    if args.probe in ("card", "all"):
        probe_card_capture(client)


if __name__ == "__main__":
    main()
