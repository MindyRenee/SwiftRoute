"""Kernel-level WireGuard tunnel manager."""

import base64
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from src.config import settings


@dataclass(frozen=True)
class TunnelConfig:
    """WireGuard tunnel configuration pair."""

    tunnel_id: str
    gateway_private_key: str
    gateway_public_key: str
    node_private_key: str
    node_public_key: str
    gateway_endpoint: str
    node_ip: str
    gateway_ip: str
    listen_port: int
    allowed_ips: str


class TunnelManagerError(Exception):
    """Tunnel lifecycle failure."""


class WireGuardTunnelManager:
    """Creates secure kernel-level WireGuard tunnels for agent workloads."""

    def __init__(self, config_path: str = settings.wireguard_config_path):
        self.config_path = Path(config_path)
        self._tunnels: dict[str, TunnelConfig] = {}
        self._ip_counter = 2

    @staticmethod
    def _generate_keypair() -> tuple[str, str]:
        private_key = X25519PrivateKey.generate()
        public_key = private_key.public_key()
        priv_b64 = base64.b64encode(
            private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode()
        pub_b64 = base64.b64encode(
            public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode()
        return priv_b64, pub_b64

    def create_tunnel(self, node_ip: str | None = None) -> TunnelConfig:
        """Generate a fresh WireGuard tunnel config with a unique node IP."""
        tunnel_id = str(uuid.uuid4())
        gw_priv, gw_pub = self._generate_keypair()
        node_priv, node_pub = self._generate_keypair()

        if node_ip is None:
            ip = f"10.200.200.{self._ip_counter}/24"
            self._ip_counter += 1
        else:
            ip = node_ip

        cfg = TunnelConfig(
            tunnel_id=tunnel_id,
            gateway_private_key=gw_priv,
            gateway_public_key=gw_pub,
            node_private_key=node_priv,
            node_public_key=node_pub,
            gateway_endpoint="sap-gw.example.com:51820",
            node_ip=ip,
            gateway_ip="10.200.200.1/24",
            listen_port=51820,
            allowed_ips="0.0.0.0/0",
        )
        self._tunnels[tunnel_id] = cfg
        return cfg

    def write_config(self, cfg: TunnelConfig, path: Path | None = None) -> Path:
        """Persist a WireGuard configuration file."""
        target = path or (self.config_path.parent / f"sap_{cfg.tunnel_id}.conf")
        target.parent.mkdir(parents=True, exist_ok=True)
        wg_conf = f"""[Interface]
PrivateKey = {cfg.node_private_key}
Address = {cfg.node_ip}
ListenPort = {cfg.listen_port}

[Peer]
PublicKey = {cfg.gateway_public_key}
AllowedIPs = {cfg.allowed_ips}
Endpoint = {cfg.gateway_endpoint}
PersistentKeepalive = 25
"""
        target.write_text(wg_conf)
        return target

    def bring_up(self, cfg: TunnelConfig) -> None:
        """Raise the WireGuard interface (requires root / CAP_NET_ADMIN)."""
        iface = f"sap_{cfg.tunnel_id[:8]}"
        config_file = self.write_config(cfg)
        try:
            subprocess.run(
                ["wg-quick", "up", str(config_file)],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise TunnelManagerError("wg-quick not found; install WireGuard tools") from exc
        except subprocess.CalledProcessError as exc:
            raise TunnelManagerError(f"Failed to bring up {iface}: {exc.stderr}") from exc

    def tear_down(self, cfg: TunnelConfig) -> None:
        """Destroy the tunnel interface."""
        config_file = self.config_path.parent / f"sap_{cfg.tunnel_id}.conf"
        try:
            subprocess.run(
                ["wg-quick", "down", str(config_file)],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            pass
        if config_file.exists():
            config_file.unlink()
        self._tunnels.pop(cfg.tunnel_id, None)

    def teardown_all(self) -> None:
        """Destroy every managed tunnel."""
        for cfg in list(self._tunnels.values()):
            self.tear_down(cfg)
