# tests/test_api_cli.py
import importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_api_port_defaults_to_18000():
    # _api_port() reads the env at call-time, so module import caching is fine.
    os.environ.pop("BSESSION_API_PORT", None)
    from lib import api
    assert api._api_port() == 18000


def test_api_port_reads_env():
    os.environ["BSESSION_API_PORT"] = "9999"
    from lib import api
    assert api._api_port() == 9999
    os.environ.pop("BSESSION_API_PORT", None)


def test_resolve_port_prefers_explicit_port():
    from urllib.parse import urlparse
    from lib import api
    h = api.Handler.__new__(api.Handler)
    assert h._resolve_screenshot_port(urlparse("/screenshot?port=9300")) == 9300


def test_resolve_port_from_profile(tmp_state="/tmp/bs-test-state"):
    import shutil
    os.environ["BSESSION_STATE_DIR"] = tmp_state
    shutil.rmtree(tmp_state, ignore_errors=True)
    try:
        import importlib
        from lib import state as _state
        importlib.reload(_state)
        _state.insert_chrome("demo", 9333, 4242)
        from urllib.parse import urlparse
        from lib import api
        importlib.reload(api)
        h = api.Handler.__new__(api.Handler)
        assert h._resolve_screenshot_port(urlparse("/captcha/screenshot?profile=demo")) == 9333
    finally:
        shutil.rmtree(tmp_state, ignore_errors=True)
        os.environ.pop("BSESSION_STATE_DIR", None)


def test_resolve_port_unknown_profile_returns_none(tmp_state="/tmp/bs-test-state2"):
    import shutil
    os.environ["BSESSION_STATE_DIR"] = tmp_state
    shutil.rmtree(tmp_state, ignore_errors=True)
    try:
        import importlib
        from lib import state as _state
        importlib.reload(_state)
        _state.insert_chrome("demo", 9333, 4242)   # some other profile exists
        from urllib.parse import urlparse
        from lib import api
        importlib.reload(api)
        h = api.Handler.__new__(api.Handler)
        assert h._resolve_screenshot_port(urlparse("/screenshot?profile=ghost")) is None
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
