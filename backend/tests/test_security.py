"""Security behaviour without network: auth on every case route, timeout reconciliation semantics."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from countersign import api
from countersign.config import Settings
from countersign.paypal import PayPalTimeout
from countersign.service import Countersign

CASE_ROUTES = [("post", "/api/cases"), ("get", "/api/cases/00000000-0000-0000-0000-000000000000"),
               ("post", "/api/cases/00000000-0000-0000-0000-000000000000/mandate"),
               ("post", "/api/cases/00000000-0000-0000-0000-000000000000/agent"),
               ("post", "/api/cases/00000000-0000-0000-0000-000000000000/verify")]


TEST_SETTINGS = Settings("id", "secret", "https://api-m.sandbox.paypal.com", "postgresql://unused", None, "right-token")


@pytest.mark.parametrize("method,path", CASE_ROUTES)
@pytest.mark.parametrize("header", [None, "Bearer wrong-token", "wrong-scheme"])
def test_case_routes_reject_missing_or_wrong_token(method, path, header, monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: TEST_SETTINGS)  # no .env needed: runs in a clean clone
    client = TestClient(api.app)  # no lifespan: auth must fail before any DB or PayPal access
    headers = {"Authorization": header} if header else {}
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(client, method)(path, headers=headers, **kwargs).status_code == 401


class FakePayPal:
    def __init__(self, timeouts: int):
        self.timeouts, self.request_ids = timeouts, []

    def refund(self, capture_id, amount, currency, request_id):
        self.request_ids.append(request_id)
        if len(self.request_ids) <= self.timeouts:
            raise PayPalTimeout("simulated timeout after send")
        return {"id": "R1", "status": "COMPLETED", "amount": {"value": str(amount)}}


def service_with(fake: FakePayPal) -> Countersign:
    settings = Settings("id", "secret", "https://api-m.sandbox.paypal.com", "postgresql://unused", None, "t")
    return Countersign(settings, fake)


ROW = {"capture_id": "CAP1", "amount": Decimal("0.25"), "currency": "USD"}


def test_timeout_is_reconciled_with_the_same_paypal_request_id():
    fake = FakePayPal(timeouts=1)
    body = service_with(fake)._refund_with_reconcile(ROW, "req-123")
    assert body["id"] == "R1" and fake.request_ids == ["req-123", "req-123"]


def test_second_timeout_surfaces_as_unknown_not_a_new_request():
    fake = FakePayPal(timeouts=2)
    with pytest.raises(PayPalTimeout):
        service_with(fake)._refund_with_reconcile(ROW, "req-123")
    assert fake.request_ids == ["req-123", "req-123"]
