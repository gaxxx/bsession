# tests/test_secret_fill.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_positional_value_wins():
    from lib import cli
    assert cli._resolve_input_value("hello", None) == "hello"


def test_reads_from_env_when_no_positional():
    from lib import cli
    os.environ["BS_TEST_SECRET"] = "s3cr3t"
    try:
        assert cli._resolve_input_value(None, "BS_TEST_SECRET") == "s3cr3t"
    finally:
        os.environ.pop("BS_TEST_SECRET", None)


def test_missing_env_var_errors():
    from lib import cli
    raised = False
    try:
        cli._resolve_input_value(None, "BS_TEST_NOPE")
    except SystemExit:
        raised = True
    assert raised


def test_neither_provided_errors():
    from lib import cli
    raised = False
    try:
        cli._resolve_input_value(None, None)
    except SystemExit:
        raised = True
    assert raised


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for f in fns:
        try: f(); print(f"  PASS {f.__name__}")
        except Exception as e: bad += 1; print(f"  FAIL {f.__name__}: {e}")
    print(f"{len(fns)-bad}/{len(fns)} passed"); return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
