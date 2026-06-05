"""Tests for the Firecracker MicroVM manager."""

import base64

from src.vm_manager import MicroVMManager


class TestMicroVMManager:
    async def test_create_and_destroy_vm(self) -> None:
        mgr = MicroVMManager()
        script = base64.b64encode(b"print('hello')").decode()
        vm = await mgr.create_vm(script)
        assert vm.vm_id
        assert vm.pid is not None
        assert vm.vm_id in mgr._vms

        logs = await mgr.execute(vm)
        assert logs["status"] == "completed"
        assert logs["signature"]

        await mgr.destroy(vm)
        assert vm._destroyed is True
        assert vm.vm_id not in mgr._vms

    async def test_destroy_all(self) -> None:
        mgr = MicroVMManager()
        script = base64.b64encode(b"print('hello')").decode()
        vm1 = await mgr.create_vm(script)
        vm2 = await mgr.create_vm(script)
        assert len(mgr._vms) == 2

        await mgr.destroy_all()
        assert len(mgr._vms) == 0
        assert vm1._destroyed is True
        assert vm2._destroyed is True

    async def test_idempotent_destroy(self) -> None:
        mgr = MicroVMManager()
        script = base64.b64encode(b"print('hello')").decode()
        vm = await mgr.create_vm(script)
        await mgr.destroy(vm)
        await mgr.destroy(vm)  # should not raise
        assert vm._destroyed is True

    async def test_mock_mode_no_subprocess(self) -> None:
        from src.config import settings
        old_mode = settings.vm_mode
        settings.vm_mode = "mock"
        mgr = MicroVMManager()
        script = base64.b64encode(b"print('hello')").decode()
        vm = await mgr.create_vm(script)
        assert vm._proc is None
        assert vm.pid is None
        logs = await mgr.execute(vm)
        assert logs["status"] == "completed"
        await mgr.destroy(vm)
        settings.vm_mode = old_mode
