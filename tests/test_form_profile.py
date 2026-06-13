# tests/test_form_profile.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_bsession_profile_env_honored_without_form():
    # Documented behavior: BSESSION_PROFILE overrides the profile even when
    # no form file is in play (the scratch context).
    from lib import form
    os.environ.pop("BSESSION_FORM", None)
    os.environ["BSESSION_PROFILE"] = "nas-login"
    try:
        ctx = form.resolve()
        assert ctx.profile == "nas-login", f"got {ctx.profile!r}"
    finally:
        os.environ.pop("BSESSION_PROFILE", None)


def test_profile_override_arg_beats_env():
    from lib import form
    os.environ.pop("BSESSION_FORM", None)
    os.environ["BSESSION_PROFILE"] = "from-env"
    try:
        ctx = form.resolve(profile_override="from-arg")
        assert ctx.profile == "from-arg"
    finally:
        os.environ.pop("BSESSION_PROFILE", None)


def test_defaults_to_scratch_when_nothing_set():
    from lib import form
    os.environ.pop("BSESSION_FORM", None)
    os.environ.pop("BSESSION_PROFILE", None)
    ctx = form.resolve()
    assert ctx.profile == "__scratch__"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for f in fns:
        try: f(); print(f"  PASS {f.__name__}")
        except Exception as e: bad += 1; print(f"  FAIL {f.__name__}: {e}")
    print(f"{len(fns)-bad}/{len(fns)} passed"); return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
