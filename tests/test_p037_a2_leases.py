"""Real flock competition: copied context is not a live or task-owned grant."""
import asyncio,contextvars,time
from threading import Event
import anyio,pytest
from infra.file_gate import file_gate
from packages.config.errors import ContractError


@pytest.mark.parametrize('cancel_parent',[False,True])
def test_detached_child_cannot_reuse_released_or_cancelled_parent(tmp_path,cancel_parent):
    async def run():
        gate=file_gate(tmp_path/'gate',wait_timeout=.04,poll_seconds=.002);ready=asyncio.Event();children=[]
        async def child():
            await ready.wait();assert not gate.held()
            with pytest.raises(ContractError) as e:
                async with gate.async_hold():pytest.fail('stale authority crossed a real competing flock')
            assert e.value.code=='RESOURCE_LOCK_TIMEOUT'
        async def parent():
            async with gate.async_hold():
                children.append(asyncio.create_task(child()))
                if cancel_parent:raise asyncio.CancelledError()
        if cancel_parent:
            with pytest.raises(asyncio.CancelledError):await parent()
        else:await parent()
        competitor=gate.try_acquire_raw();assert competitor is not None
        try:ready.set();await children[0]
        finally:gate.release_raw(competitor)
        async with gate.async_hold():assert gate.held()
    asyncio.run(run())


def test_child_is_not_owner_even_while_parent_lease_is_live(tmp_path):
    async def run():
        gate=file_gate(tmp_path/'gate',wait_timeout=.03,poll_seconds=.002)
        async def child():
            assert not gate.held()
            with pytest.raises(ContractError):
                async with gate.async_hold():pytest.fail('child borrowed a live parent task lease')
        async with gate.async_hold():
            async with gate.async_hold():assert gate.held() # Same-task reentrancy remains legal.
            await asyncio.create_task(child())
    asyncio.run(run())


def test_controlled_worker_bridge_has_lease_and_copied_later_context_does_not(tmp_path):
    gate=file_gate(tmp_path/'gate',wait_timeout=.05);captured=[]
    async def run():
        def work():
            assert gate.held();captured.append(contextvars.copy_context())
            with gate.hold():assert gate.held()
            fd=gate.try_acquire_raw();assert fd is None # Real file remains locked.
            return 7
        assert await gate.run_sync(work)==7
    asyncio.run(run());assert not captured[0].run(gate.held)
    fd=gate.try_acquire_raw();assert fd is not None;gate.release_raw(fd)


def test_uncontrolled_thread_offload_is_not_an_implicit_grant(tmp_path):
    async def run():
        gate=file_gate(tmp_path/'gate',wait_timeout=.03)
        async with gate.async_hold():assert not await anyio.to_thread.run_sync(gate.held)
    asyncio.run(run())


def test_cancelled_worker_bridge_keeps_lock_until_worker_has_exited(tmp_path):
    async def run():
        gate=file_gate(tmp_path/'gate',wait_timeout=.1);started=Event();finish=Event();done=anyio.Event();scopes=[]
        def work():started.set();assert finish.wait(2);assert gate.held()
        async def owner():
            with anyio.CancelScope() as scope:scopes.append(scope);await gate.run_sync(work)
            done.set()
        async with anyio.create_task_group() as tg:
            tg.start_soon(owner)
            while not started.is_set():await anyio.sleep(.002)
            scopes[0].cancel();assert gate.try_acquire_raw() is None
            finish.set();await done.wait()
        fd=gate.try_acquire_raw();assert fd is not None;gate.release_raw(fd)
    anyio.run(run)


def test_native_asyncio_cancellation_does_not_unlock_a_running_worker(tmp_path):
    async def run():
        gate=file_gate(tmp_path/'gate',wait_timeout=.1);started=Event();finish=Event();exited=Event()
        def work():
            try:started.set();assert finish.wait(2);assert gate.held()
            finally:exited.set()
        task=asyncio.create_task(gate.run_sync(work))
        while not started.is_set():await asyncio.sleep(.002)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert gate.try_acquire_raw() is None
        finish.set()
        with anyio.fail_after(2):
            while True:
                fd=gate.try_acquire_raw()
                if fd is not None:break
                await asyncio.sleep(.002)
        assert exited.is_set();gate.release_raw(fd)
    asyncio.run(run())
