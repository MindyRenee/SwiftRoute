"""Tests for the WireGuard tunnel manager."""

from pathlib import Path

from src.tunnel_manager import TunnelConfig, WireGuardTunnelManager


class TestWireGuardTunnelManager:
    def test_create_tunnel(self) -> None:
        mgr = WireGuardTunnelManager()
        cfg = mgr.create_tunnel()
        assert isinstance(cfg, TunnelConfig)
        assert cfg.tunnel_id
        assert cfg.gateway_private_key
        assert cfg.gateway_public_key
        assert cfg.node_private_key
        assert cfg.node_public_key
        assert cfg.tunnel_id in mgr._tunnels

    def test_write_config(self, tmp_path: Path) -> None:
        mgr = WireGuardTunnelManager()
        cfg = mgr.create_tunnel()
        out_path = tmp_path / "wg.conf"
        written = mgr.write_config(cfg, out_path)
        assert written.exists()
        text = written.read_text()
        assert "[Interface]" in text
        assert cfg.node_private_key in text
        assert cfg.gateway_public_key in text

    def test_teardown_removes_tunnel(self, tmp_path: Path) -> None:
        mgr = WireGuardTunnelManager()
        cfg = mgr.create_tunnel()
        mgr.write_config(cfg, tmp_path / f"sap_{cfg.tunnel_id}.conf")
        mgr.tear_down(cfg)
        assert cfg.tunnel_id not in mgr._tunnels

    def test_keypair_generation(self) -> None:
        mgr = WireGuardTunnelManager()
        priv, pub = mgr._generate_keypair()
        assert priv != pub
        assert len(priv) > 20
        assert len(pub) > 20

    def test_create_tunnel_auto_increments_ip(self) -> None:
        mgr = WireGuardTunnelManager()
        cfg1 = mgr.create_tunnel()
        cfg2 = mgr.create_tunnel()
        assert cfg1.node_ip != cfg2.node_ip
        assert "10.200.200." in cfg1.node_ip
        assert "10.200.200." in cfg2.node_ip
