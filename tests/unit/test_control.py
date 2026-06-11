from runtime.control import Abort, Approve, ControlPlane, Pause, Resume, Steer


def test_submit_abort_sets_flag():
    cp = ControlPlane()
    cp.submit(Abort())
    assert cp.abort_requested is True


def test_drain_returns_in_order_and_empties():
    cp = ControlPlane()
    cp.submit(Steer("a"))
    cp.submit(Pause())
    drained = cp.drain_nowait()
    assert isinstance(drained[0], Steer) and isinstance(drained[1], Pause)
    assert cp.drain_nowait() == []


async def test_wait_decision_preserves_non_decisions():
    cp = ControlPlane()
    cp.submit(Steer("先转向"))
    cp.submit(Approve(["t1"]))
    assert (await cp.wait_decision()).ids == ["t1"]
    assert isinstance(cp.drain_nowait()[0], Steer)


async def test_wait_resume():
    cp = ControlPlane()
    cp.submit(Resume())
    assert isinstance(await cp.wait_resume(), Resume)
