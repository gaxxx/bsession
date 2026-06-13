# tests/test_reaper.py
import importlib, os, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fresh_state(tmp_state):
    os.environ["BSESSION_STATE_DIR"] = tmp_state
    shutil.rmtree(tmp_state, ignore_errors=True)
    from lib import state as _state
    importlib.reload(_state)
    return _state


def _patched_reaper(_state, alive_ports):
    """Reload reaper against the test state, with chrome/ab calls stubbed."""
    from lib import reaper
    importlib.reload(reaper)
    reaper.state = _state
    calls = {"stopped": [], "closed": []}
    reaper.stop_chrome = lambda port: calls["stopped"].append(port)
    reaper.chrome_alive = lambda port: port in alive_ports
    reaper.close_session = lambda profile: calls["closed"].append(profile)
    return reaper, calls


def test_reap_closes_idle_chrome(tmp_state="/tmp/bs-test-reap1"):
    try:
        _state = _fresh_state(tmp_state)
        _state.insert_chrome("oldie", 9301, 111)
        with _state._db() as db:
            db.execute("UPDATE chromes SET last_used = 1000.0 WHERE profile = 'oldie'")
            db.commit()
        reaper, calls = _patched_reaper(_state, alive_ports={9301})

        reaped = reaper.reap_idle(ttl=1800, now=1000.0 + 1801)
        assert reaped == ["oldie"]
        assert calls["stopped"] == [9301]
        assert calls["closed"] == ["oldie"]
        assert _state.get_chrome("oldie") is None
    finally:
        shutil.rmtree(tmp_state, ignore_errors=True)
        os.environ.pop("BSESSION_STATE_DIR", None)


def test_reap_keeps_fresh_chrome(tmp_state="/tmp/bs-test-reap2"):
    try:
        _state = _fresh_state(tmp_state)
        _state.insert_chrome("fresh", 9302, 222)
        with _state._db() as db:
            db.execute("UPDATE chromes SET last_used = 1000.0 WHERE profile = 'fresh'")
            db.commit()
        reaper, calls = _patched_reaper(_state, alive_ports={9302})

        reaped = reaper.reap_idle(ttl=1800, now=1000.0 + 60)
        assert reaped == []
        assert calls["stopped"] == []
        assert _state.get_chrome("fresh") is not None
    finally:
        shutil.rmtree(tmp_state, ignore_errors=True)
        os.environ.pop("BSESSION_STATE_DIR", None)


def test_reap_drops_dead_rows_even_if_fresh(tmp_state="/tmp/bs-test-reap3"):
    # Row whose Chrome already died (e.g. container restart): clean it up and
    # close its leaked agent-browser session daemon regardless of last_used.
    try:
        _state = _fresh_state(tmp_state)
        _state.insert_chrome("ghost", 9303, 333)  # last_used = now
        reaper, calls = _patched_reaper(_state, alive_ports=set())

        reaped = reaper.reap_idle(ttl=1800)
        assert reaped == ["ghost"]
        assert calls["closed"] == ["ghost"]
        assert _state.get_chrome("ghost") is None
    finally:
        shutil.rmtree(tmp_state, ignore_errors=True)
        os.environ.pop("BSESSION_STATE_DIR", None)


def test_reap_disabled_when_ttl_zero(tmp_state="/tmp/bs-test-reap4"):
    try:
        _state = _fresh_state(tmp_state)
        _state.insert_chrome("oldie", 9304, 444)
        with _state._db() as db:
            db.execute("UPDATE chromes SET last_used = 1000.0")
            db.commit()
        reaper, calls = _patched_reaper(_state, alive_ports=set())

        assert reaper.reap_idle(ttl=0, now=999999.0) == []
        assert calls["stopped"] == []
        assert _state.get_chrome("oldie") is not None
    finally:
        shutil.rmtree(tmp_state, ignore_errors=True)
        os.environ.pop("BSESSION_STATE_DIR", None)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for f in fns:
        try:
            f(); print(f"  PASS {f.__name__}")
        except Exception as e:
            bad += 1; print(f"  FAIL {f.__name__}: {e}")
    print(f"{len(fns)-bad}/{len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
