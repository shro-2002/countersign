"""Minimal PayPal SANDBOX client. Every request asserts the sandbox host. Returns real responses only."""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

import httpx

from .config import SANDBOX_BASE, Settings
from .money import fmt

log = logging.getLogger("countersign.paypal")
SANDBOX_HOST = urlparse(SANDBOX_BASE).hostname
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# Published PayPal sandbox test card (developer.paypal.com/sandbox-testing/card-testing). Demo payments only.
SANDBOX_TEST_CARD = {"name": "Countersign Demo Buyer", "number": "4012888888881881", "expiry": "2030-12",
                     "security_code": "123"}


class PayPalError(RuntimeError):
    def __init__(self, status: int, issue: str | None, debug_id: str | None, body: dict):
        super().__init__(f"PayPal HTTP {status} {issue or ''} debug_id={debug_id}")
        self.status, self.issue, self.debug_id, self.body = status, issue, debug_id, body


class PayPalTimeout(RuntimeError):
    """Outcome unknown: the request may or may not have executed. Reconcile before retrying."""


@dataclass(frozen=True)
class OrderState:
    order_id: str
    capture_id: str
    capture_status: str
    capture_amount: Decimal
    currency: str
    refunds: list[dict]
    raw: dict


class PayPalSandbox:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._token: str | None = None
        self._token_expiry = 0.0
        self._http = httpx.Client(base_url=SANDBOX_BASE, timeout=TIMEOUT)

    def _url_guard(self, path: str) -> None:
        if urlparse(str(self._http.base_url.join(path))).hostname != SANDBOX_HOST:
            raise RuntimeError("non-sandbox PayPal host blocked")

    def _auth_header(self) -> dict:
        if not self._token or time.time() > self._token_expiry - 60:
            resp = self._http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                                   auth=(self._settings.paypal_client_id, self._settings.paypal_client_secret))
            if resp.status_code != 200:
                raise PayPalError(resp.status_code, resp.json().get("error"), None, {})
            body = resp.json()
            self._token, self._token_expiry = body["access_token"], time.time() + int(body["expires_in"])
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, *, json: dict | None = None, request_id: str | None = None,
                 extra_headers: dict | None = None) -> dict:
        self._url_guard(path)
        headers = {**self._auth_header(), "Content-Type": "application/json", **(extra_headers or {})}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        started = time.perf_counter()
        try:
            resp = self._http.request(method, path, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            log.warning("paypal timeout", extra={"path": path, "request_id": request_id})
            raise PayPalTimeout(f"{method} {path} timed out") from exc
        body = resp.json() if resp.content else {}
        log.info("paypal call", extra={"method": method, "path": path, "status": resp.status_code,
                                       "debug_id": resp.headers.get("paypal-debug-id"),
                                       "ms": round((time.perf_counter() - started) * 1000)})
        if resp.status_code >= 300:
            issue = (body.get("details") or [{}])[0].get("issue") or body.get("name")
            raise PayPalError(resp.status_code, issue, body.get("debug_id"), body)
        return body

    def create_demo_payment(self, amount: Decimal, reference: str) -> OrderState:
        """Demo setup: a real sandbox card payment, captured immediately."""
        body = {"intent": "CAPTURE",
                "purchase_units": [{"reference_id": reference,
                                    "amount": {"currency_code": "USD", "value": fmt(amount)}}],
                "payment_source": {"card": SANDBOX_TEST_CARD}}
        order = self._request("POST", "/v2/checkout/orders", json=body, request_id=f"demo-payment-{reference}",
                              extra_headers={"Prefer": "return=representation"})
        return self.parse_order(order)

    def get_order(self, order_id: str) -> OrderState:
        return self.parse_order(self._request("GET", f"/v2/checkout/orders/{order_id}"))

    def refund(self, capture_id: str, amount: Decimal, currency: str, request_id: str) -> dict:
        return self._request("POST", f"/v2/payments/captures/{capture_id}/refund", request_id=request_id,
                             json={"amount": {"currency_code": currency, "value": fmt(amount)}},
                             extra_headers={"Prefer": "return=representation"})

    @staticmethod
    def parse_order(order: dict) -> OrderState:
        payments = (order.get("purchase_units") or [{}])[0].get("payments") or {}
        captures = payments.get("captures") or []
        if not captures:
            raise PayPalError(422, "NO_CAPTURE", order.get("debug_id"), order)
        capture = captures[0]
        refunds = [{"refund_id": r["id"], "capture_id": capture["id"], "status": r["status"],
                    "amount": r["amount"]["value"], "currency": r["amount"]["currency_code"],
                    "create_time": r.get("create_time")} for r in payments.get("refunds", [])]
        return OrderState(order["id"], capture["id"], capture["status"], Decimal(capture["amount"]["value"]),
                          capture["amount"]["currency_code"], refunds, order)
