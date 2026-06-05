"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """SAP runtime settings."""

    model_config = SettingsConfigDict(
        env_prefix="SAP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Gateway
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Protocol fees
    transaction_fee_rate: float = 0.04
    signature_mint_fee_usd: float = 0.05

    # Local baseline workload (10 kWh)
    local_compute_cost_usd: float = 3.50
    local_carbon_g: float = 4500.0
    local_tax_value_usd: float = 0.00

    # Grid API
    grid_api_url: str = "https://api.example-grid.com/v1"
    grid_api_key: str = ""

    # VM / Tunnel
    vm_mode: str = "mock"  # "mock" | "firecracker"
    vm_kernel_path: str = "/opt/sap/vmlinux"
    vm_rootfs_path: str = "/opt/sap/rootfs.ext4"
    wireguard_config_path: str = "/etc/wireguard/sap0.conf"

    # SGE
    sge_enabled: bool = True
    sge_peak_threshold_usd_per_mwh: float = 5000.0
    sge_pause_minutes: int = 15

    # Receipt signing keys (paths to PEM files; generated on first run if missing)
    receipt_private_key_path: str = "./data/receipt_private.pem"
    receipt_public_key_path: str = "./data/receipt_public.pem"

    # Persistence
    db_path: str = "./data/sap.db"

    # Job & rate-limit housekeeping
    job_max_age_seconds: int = 86400  # 24 hours
    rate_limit_max_entries: int = 10000

    # Payments
    payment_mode: str = "mock"  # "mock" | "stripe"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    payout_threshold_usd: float = 10.0


settings = Settings()
