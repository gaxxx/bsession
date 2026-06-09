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
