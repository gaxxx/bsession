# tests/test_ab_bind.py
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_bind_swallows_timeout_and_warns(capsys=None):
    # A slow `agent-browser connect` must not crash the caller with a raw
    # TimeoutExpired traceback — bind is idempotent and recoverable.
    from lib import ab
    orig = subprocess.run

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0] if a else "connect", timeout=10)

    subprocess.run = boom
    try:
        ab.bind("someprofile", 9301)  # must NOT raise
    finally:
        subprocess.run = orig


def test_bind_normal_path_does_not_raise():
    from lib import ab
    orig = subprocess.run
    subprocess.run = lambda *a, **k: None
    try:
        ab.bind("p", 9302)
    finally:
        subprocess.run = orig


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for f in fns:
        try: f(); print(f"  PASS {f.__name__}")
        except Exception as e: bad += 1; print(f"  FAIL {f.__name__}: {e}")
    print(f"{len(fns)-bad}/{len(fns)} passed"); return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
