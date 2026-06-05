"""Tests for the GHG receipt generator."""

import json

from src.receipts import GHGReceipt, ReceiptGenerator


class TestReceiptGenerator:
    def test_generate_receipt(self) -> None:
        receipt = ReceiptGenerator.generate(
            job_id="job-123",
            carbon_diverted_g=4150.0,
            carbon_remote_g=350.0,
            carbon_local_g=4500.0,
            tax_credit_usd=0.26,
            zone_name="West Texas Solar Surplus",
            zone_region="us-south",
        )
        assert isinstance(receipt, GHGReceipt)
        assert receipt.job_id == "job-123"
        assert receipt.carbon_diverted_g == 4150.0
        assert receipt.tax_credit_usd == 0.26
        assert receipt.zone_name == "West Texas Solar Surplus"
        assert receipt.scope == "Scope 3"
        assert receipt.signature
        assert receipt.receipt_id
        assert receipt.timestamp_utc

    def test_signature_is_non_deterministic(self) -> None:
        r1 = ReceiptGenerator.generate(
            job_id="job-123",
            carbon_diverted_g=100.0,
            carbon_remote_g=50.0,
            carbon_local_g=150.0,
            tax_credit_usd=1.0,
            zone_name="Z",
            zone_region="R",
        )
        r2 = ReceiptGenerator.generate(
            job_id="job-123",
            carbon_diverted_g=100.0,
            carbon_remote_g=50.0,
            carbon_local_g=150.0,
            tax_credit_usd=1.0,
            zone_name="Z",
            zone_region="R",
        )
        # Same inputs produce different receipts (unique IDs/timestamps)
        assert r1.receipt_id != r2.receipt_id
        assert r1.signature != r2.signature

    def test_verify_receipt(self) -> None:
        receipt = ReceiptGenerator.generate(
            job_id="job-123",
            carbon_diverted_g=100.0,
            carbon_remote_g=50.0,
            carbon_local_g=150.0,
            tax_credit_usd=1.0,
            zone_name="Z",
            zone_region="R",
        )
        assert ReceiptGenerator.verify(receipt) is True

    def test_to_json(self) -> None:
        receipt = ReceiptGenerator.generate(
            job_id="job-123",
            carbon_diverted_g=100.0,
            carbon_remote_g=50.0,
            carbon_local_g=150.0,
            tax_credit_usd=1.0,
            zone_name="Z",
            zone_region="R",
        )
        text = ReceiptGenerator.to_json(receipt)
        data = json.loads(text)
        assert data["job_id"] == "job-123"
        assert data["scope"] == "Scope 3"
        assert "signature" in data
