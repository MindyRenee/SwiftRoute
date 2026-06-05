"""Tests for the SAP API gateway."""

import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.gateway import app


@pytest.fixture
def client() -> TestClient:
    from src.config import settings
    old_mode = settings.payment_mode
    old_key = settings.stripe_secret_key
    settings.payment_mode = "mock"
    settings.stripe_secret_key = ""
    with TestClient(app) as c:
        yield c
    settings.payment_mode = old_mode
    settings.stripe_secret_key = old_key


class TestRootEndpoints:
    def test_root(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["protocol"] == "SAP"

    def test_health(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestTunnelRouteAndPrice:
    def test_returns_live_route_and_pricing(self, client: TestClient) -> None:
        r = client.get("/tunnel_route_and_price")
        assert r.status_code == 200
        data = r.json()
        assert "optimal_zone" in data
        assert "pricing" in data
        assert data["pricing"]["is_profitable"] is True
        assert "ticket_endpoint" in data
        assert "parameters" in data

    def test_llms_full_txt(self, client: TestClient) -> None:
        r = client.get("/llms-full.txt")
        assert r.status_code == 200
        assert "SubGrid Automaton Protocol" in r.text
        assert "/ticket" in r.text


class TestTicketEndpoint:
    def test_buy_ticket_success(self, client: TestClient) -> None:
        script = base64.b64encode(b"print('hello')").decode()
        r = client.post(
            "/ticket",
            json={
                "action": "buy_subgrid_compute_ticket",
                "execution_script_b64": script,
                "max_budget_usd": 5.0,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        assert data["job_id"]
        assert data["zone"]["id"]
        assert data["pricing"]["delta_net"]
        assert data["receipt"]["signature"]
        assert data["vm_logs"]["status"] == "completed"
        assert data["payment"]["status"] == "captured"
        assert data["payment"]["payment_id"]
        assert data["payment"]["amount_usd"] > 0

    def test_buy_ticket_with_payment_method(self, client: TestClient) -> None:
        script = base64.b64encode(b"print('hello')").decode()
        r = client.post(
            "/ticket",
            json={
                "action": "buy_subgrid_compute_ticket",
                "execution_script_b64": script,
                "max_budget_usd": 5.0,
                "stripe_payment_method_id": "pm_test_123",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["payment"]["status"] == "captured"

    def test_invalid_base64(self, client: TestClient) -> None:
        r = client.post(
            "/ticket",
            json={
                "action": "buy_subgrid_compute_ticket",
                "execution_script_b64": "not-valid-base64!!!",
            },
        )
        assert r.status_code == 400
        assert "Invalid base64" in r.json()["detail"]

    def test_unknown_action(self, client: TestClient) -> None:
        r = client.post(
            "/ticket",
            json={
                "action": "do_something_else",
                "execution_script_b64": base64.b64encode(b"x").decode(),
            },
        )
        assert r.status_code == 400
        assert "Unknown action" in r.json()["detail"]

    def test_budget_too_low(self, client: TestClient) -> None:
        script = base64.b64encode(b"print('hello')").decode()
        r = client.post(
            "/ticket",
            json={
                "action": "buy_subgrid_compute_ticket",
                "execution_script_b64": script,
                "max_budget_usd": 0.01,
            },
        )
        assert r.status_code == 402
        assert "max_budget_usd" in r.json()["detail"]

    def test_script_size_limit(self, client: TestClient) -> None:
        big = "x" * (11 * 1024 * 1024)
        script = base64.b64encode(big.encode()).decode()
        r = client.post(
            "/ticket",
            json={
                "action": "buy_subgrid_compute_ticket",
                "execution_script_b64": script,
            },
        )
        assert r.status_code == 400
        assert "exceeds" in r.json()["detail"]

    def test_dangerous_script_rejected(self, client: TestClient) -> None:
        script = base64.b64encode(b"import os; os.system('rm -rf /')").decode()
        r = client.post(
            "/ticket",
            json={
                "action": "buy_subgrid_compute_ticket",
                "execution_script_b64": script,
            },
        )
        assert r.status_code == 400
        assert "forbidden" in r.json()["detail"].lower()


class TestGridWebhook:
    def test_grid_webhook_peak(self, client: TestClient) -> None:
        from src.gateway import _job_registry
        _job_registry.add("job-abc")
        r = client.post(
            "/webhook/grid",
            json={
                "event_type": "peak_demand",
                "zone_id": "tx-solar-01",
                "price_usd_per_mwh": 6000.0,
                "timestamp": "2026-01-01T00:00:00Z",
                "job_id": "job-abc",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "frozen"
        assert data["job_id"] == "job-abc"

    def test_grid_webhook_invalid_event_type(self, client: TestClient) -> None:
        from src.gateway import _job_registry
        _job_registry.add("job-abc")
        r = client.post(
            "/webhook/grid",
            json={
                "event_type": "unknown_event",
                "zone_id": "z",
                "price_usd_per_mwh": 100.0,
                "timestamp": "2026-01-01T00:00:00Z",
                "job_id": "job-abc",
            },
        )
        assert r.status_code == 400
        assert "Invalid event_type" in r.json()["detail"]

    def test_grid_webhook_unknown_job(self, client: TestClient) -> None:
        r = client.post(
            "/webhook/grid",
            json={
                "event_type": "peak_demand",
                "zone_id": "tx-solar-01",
                "price_usd_per_mwh": 6000.0,
                "timestamp": "2026-01-01T00:00:00Z",
                "job_id": "job-unknown",
            },
        )
        assert r.status_code == 404
        assert "Unknown job_id" in r.json()["detail"]


class TestCreatorEndpoints:
    def test_balance_after_ticket(self, client: TestClient) -> None:
        script = base64.b64encode(b"print('hello')").decode()
        client.post(
            "/ticket",
            json={
                "action": "buy_subgrid_compute_ticket",
                "execution_script_b64": script,
                "max_budget_usd": 5.0,
            },
        )
        r = client.get("/creator/balance")
        assert r.status_code == 200
        data = r.json()
        assert data["balance_usd"] > 0
        assert data["eligible_for_payout"] is False  # single ticket < $10 threshold

    def test_payout_below_threshold(self, client: TestClient) -> None:
        r = client.post("/creator/payout")
        assert r.status_code == 402
        assert "below threshold" in r.json()["detail"]

    def test_payout_after_multiple_tickets(self, client: TestClient) -> None:
        from src.config import settings
        old_threshold = settings.payout_threshold_usd
        settings.payout_threshold_usd = 0.10  # low threshold for testing
        script = base64.b64encode(b"print('hello')").decode()
        # Run enough tickets to exceed lowered threshold
        for _ in range(5):
            client.post(
                "/ticket",
                json={
                    "action": "buy_subgrid_compute_ticket",
                    "execution_script_b64": script,
                    "max_budget_usd": 5.0,
                },
            )
        r = client.post("/creator/payout")
        settings.payout_threshold_usd = old_threshold
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "initiated"
        assert data["amount_usd"] > 0
        assert data["payout_id"]

        # Balance should be zero after payout
        r2 = client.get("/creator/balance")
        assert r2.json()["balance_usd"] == 0.0


class TestStripeWebhook:
    def test_webhook_not_configured(self, client: TestClient) -> None:
        r = client.post("/webhook/stripe", data=b"{}")
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]

    def test_webhook_invalid_signature(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings
        settings.payment_mode = "stripe"
        settings.stripe_webhook_secret = "whsec_test"

        def fake_construct_event(*args: Any, **kwargs: Any) -> None:
            raise ValueError("Invalid signature")

        monkeypatch.setattr("stripe.Webhook.construct_event", fake_construct_event)
        r = client.post("/webhook/stripe", data=b"{}", headers={"stripe-signature": "bad"})
        assert r.status_code == 400
        assert "verification failed" in r.json()["detail"]

        settings.payment_mode = "mock"
        settings.stripe_webhook_secret = ""

    def test_webhook_charge_succeeded(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings
        settings.payment_mode = "stripe"
        settings.stripe_webhook_secret = "whsec_test"

        def fake_construct_event(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "id": "evt_123",
                "type": "charge.succeeded",
                "data": {"object": {"id": "ch_123", "amount": 5000}},
            }

        monkeypatch.setattr("stripe.Webhook.construct_event", fake_construct_event)
        r = client.post("/webhook/stripe", data=b"{}", headers={"stripe-signature": "sig"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "charge_succeeded"
        assert data["charge_id"] == "ch_123"
        assert data["amount_usd"] == 50.0

        settings.payment_mode = "mock"
        settings.stripe_webhook_secret = ""

    def test_webhook_charge_failed(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings
        settings.payment_mode = "stripe"
        settings.stripe_webhook_secret = "whsec_test"

        def fake_construct_event(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "id": "evt_456",
                "type": "charge.failed",
                "data": {"object": {"id": "ch_456", "failure_message": "card_declined"}},
            }

        monkeypatch.setattr("stripe.Webhook.construct_event", fake_construct_event)
        r = client.post("/webhook/stripe", data=b"{}", headers={"stripe-signature": "sig"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "charge_failed"
        assert data["reason"] == "card_declined"

        settings.payment_mode = "mock"
        settings.stripe_webhook_secret = ""

    def test_webhook_ignored_event(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings
        settings.payment_mode = "stripe"
        settings.stripe_webhook_secret = "whsec_test"

        def fake_construct_event(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "id": "evt_789",
                "type": "customer.created",
                "data": {"object": {"id": "cus_789"}},
            }

        monkeypatch.setattr("stripe.Webhook.construct_event", fake_construct_event)
        r = client.post("/webhook/stripe", data=b"{}", headers={"stripe-signature": "sig"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ignored"
        assert data["event_type"] == "customer.created"

        settings.payment_mode = "mock"
        settings.stripe_webhook_secret = ""


class TestStripeSetupWebhook:
    def test_setup_not_configured(self, client: TestClient) -> None:
        r = client.post("/stripe/setup-webhook", json={"url": "https://example.com/webhook/stripe"})
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]

    def test_setup_missing_url(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings
        settings.payment_mode = "stripe"
        settings.stripe_secret_key = "sk_test_123"

        r = client.post("/stripe/setup-webhook", json={})
        assert r.status_code == 422  # Pydantic validation error

        settings.payment_mode = "mock"
        settings.stripe_secret_key = ""


class TestAiTxt:
    def test_ai_txt(self, client: TestClient) -> None:
        r = client.get("/ai.txt")
        assert r.status_code == 200
        text = r.text
        assert "SubGrid Automaton Protocol" in text
        assert "Endpoints" in text
        assert "Pricing Model" in text
        assert "Rate limit: 60" in text


class TestCheckoutCreate:
    def test_checkout_not_configured(self, client: TestClient) -> None:
        r = client.post(
            "/checkout/create",
            json={
                "amount_usd": 5.0,
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
                "script_b64": "cHJpbnQoJ2hlbGxvJyk=",
            },
        )
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]

    def test_checkout_invalid_script(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.config import settings
        settings.payment_mode = "stripe"
        settings.stripe_secret_key = "sk_test_123"

        r = client.post(
            "/checkout/create",
            json={
                "amount_usd": 5.0,
                "success_url": "https://example.com/success",
                "cancel_url": "https://example.com/cancel",
                "script_b64": "aW1wb3J0IG9zOyBvcy5zeXN0ZW0oJ3JtIC1yZiAvJyk=",  # dangerous script
            },
        )
        assert r.status_code == 400
        assert "forbidden" in r.json()["detail"].lower()

        settings.payment_mode = "mock"
        settings.stripe_secret_key = ""


class TestConnectPayout:
    def test_connect_payout_below_balance(self, client: TestClient) -> None:
        r = client.post(
            "/connect/payout",
            json={"stripe_account_id": "acct_test_123", "amount_usd": 999.0},
        )
        assert r.status_code == 402
        assert "below requested" in r.json()["detail"]

    def test_connect_payout_zero_amount(self, client: TestClient) -> None:
        r = client.post(
            "/connect/payout",
            json={"stripe_account_id": "acct_test_123", "amount_usd": 0.0},
        )
        assert r.status_code == 400
        assert "Amount must be greater than 0" in r.json()["detail"]

    def test_connect_payout_success(self, client: TestClient) -> None:
        # Buy tickets first to generate creator balance
        script = base64.b64encode(b"print('hello')").decode()
        for _ in range(3):
            client.post(
                "/ticket",
                json={
                    "action": "buy_subgrid_compute_ticket",
                    "execution_script_b64": script,
                    "max_budget_usd": 5.0,
                },
            )
        r = client.post(
            "/connect/payout",
            json={"stripe_account_id": "acct_test_123", "amount_usd": 0.05},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "initiated"
        assert data["destination"] == "acct_test_123"
