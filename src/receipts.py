"""Tax receipt and GHG Scope 3 compliance generator."""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from src.config import settings


@dataclass(frozen=True)
class GHGReceipt:
    """Verified GHG Scope 3 compliance receipt."""

    receipt_id: str
    job_id: str
    timestamp_utc: str
    scope: str = "Scope 3"
    carbon_diverted_g: float = 0.0
    carbon_remote_g: float = 0.0
    carbon_local_g: float = 0.0
    tax_credit_usd: float = 0.0
    zone_name: str = ""
    zone_region: str = ""
    signature: str = ""


class ReceiptGenerator:
    """Creates signed compliance receipts for tax write-offs.

    The Ed25519 keypair is persisted to disk so receipts remain
    verifiable across process restarts.
    """

    _private_key: Ed25519PrivateKey | None = None
    _public_key: Ed25519PublicKey | None = None

    @classmethod
    def _load_or_generate_keys(cls) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
        priv_path = Path(settings.receipt_private_key_path)
        pub_path = Path(settings.receipt_public_key_path)

        if priv_path.exists() and pub_path.exists():
            priv_pem = priv_path.read_bytes()
            pub_pem = pub_path.read_bytes()
            private_key = serialization.load_pem_private_key(priv_pem, password=None)  # type: ignore[assignment]
            public_key = serialization.load_pem_public_key(pub_pem)  # type: ignore[assignment]
            return private_key, public_key  # type: ignore[return-value]

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        priv_path.parent.mkdir(parents=True, exist_ok=True)
        pub_path.parent.mkdir(parents=True, exist_ok=True)

        priv_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        pub_path.write_bytes(
            public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        return private_key, public_key

    @classmethod
    def _get_keypair(cls) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
        if cls._private_key is None:
            cls._private_key, cls._public_key = cls._load_or_generate_keys()
        assert cls._private_key is not None
        assert cls._public_key is not None
        return cls._private_key, cls._public_key

    @classmethod
    def get_public_key_pem(cls) -> str:
        _, pub = cls._get_keypair()
        return pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    @classmethod
    def verify(cls, receipt: GHGReceipt) -> bool:
        """Verify the Ed25519 signature on a receipt."""
        _, pub = cls._get_keypair()
        payload = json.dumps(
            {
                "receipt_id": receipt.receipt_id,
                "job_id": receipt.job_id,
                "timestamp": receipt.timestamp_utc,
                "scope": receipt.scope,
                "carbon_diverted_g": receipt.carbon_diverted_g,
                "tax_credit_usd": receipt.tax_credit_usd,
                "zone": receipt.zone_name,
            },
            sort_keys=True,
        )
        try:
            pub.verify(bytes.fromhex(receipt.signature), payload.encode())
            return True
        except Exception:
            return False

    @staticmethod
    def generate(
        job_id: str,
        carbon_diverted_g: float,
        carbon_remote_g: float,
        carbon_local_g: float,
        tax_credit_usd: float,
        zone_name: str,
        zone_region: str,
    ) -> GHGReceipt:
        """Mint a cryptographically signed GHG receipt."""
        receipt_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(
            {
                "receipt_id": receipt_id,
                "job_id": job_id,
                "timestamp": timestamp,
                "scope": "Scope 3",
                "carbon_diverted_g": carbon_diverted_g,
                "tax_credit_usd": tax_credit_usd,
                "zone": zone_name,
            },
            sort_keys=True,
        )
        priv, _ = ReceiptGenerator._get_keypair()
        signature = priv.sign(payload.encode()).hex()

        return GHGReceipt(
            receipt_id=receipt_id,
            job_id=job_id,
            timestamp_utc=timestamp,
            carbon_diverted_g=carbon_diverted_g,
            carbon_remote_g=carbon_remote_g,
            carbon_local_g=carbon_local_g,
            tax_credit_usd=tax_credit_usd,
            zone_name=zone_name,
            zone_region=zone_region,
            signature=signature,
        )

    @staticmethod
    def to_json(receipt: GHGReceipt) -> str:
        return json.dumps(asdict(receipt), indent=2)
