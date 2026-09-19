"""
BETFAIR CREDENTIALS - SAFE LOADER
=================================
One place for scripts to obtain Betfair credentials, with the rule that they
are **never printed, logged or written into the project**.

Look-up order:
  1. environment variables  BETFAIR_APP_KEY / BETFAIR_SESSION / BETFAIR_USERNAME
  2. the shared config       E:\\CGMBET\\betfair_api_config.json   (read-only)

Use:
    from betfair_creds import get, mask, have
    app_key, session = get("app_key"), get("session")
    print("using app key", mask(app_key))       # never the raw value

`mask()` is the only safe way to show anything about a credential.
"""
from __future__ import annotations

import json
import os
import sys

CONFIG_CANDIDATES = [
    os.environ.get("BETFAIR_CONFIG", ""),
    r"E:\CGMBET\betfair_api_config.json",
    os.path.join(os.environ.get("APPDATA", ""), "racing-odds", "betfair.json"),
]

_ENV = {
    "app_key": "BETFAIR_APP_KEY",
    "session": "BETFAIR_SESSION",
    "username": "BETFAIR_USERNAME",
    "password": "BETFAIR_PASSWORD",
}

_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    cfg = {}
    for path in CONFIG_CANDIDATES:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cfg.update({k.lower(): v for k, v in json.load(f).items()})
            except Exception:
                pass
    _cache = cfg
    return cfg


def get(name, default=None):
    """Credential by name. Environment wins over the config file."""
    env = os.environ.get(_ENV.get(name, ""), "")
    if env:
        return env
    return _load().get(name.lower(), default)


def have(*names):
    """True when every named credential is available."""
    return all(get(n) for n in names)


def mask(value):
    """Safe display form: never reveal more than the two ends."""
    v = "" if value is None else str(value)
    if not v:
        return "(missing)"
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:2]}{'*' * min(len(v) - 4, 12)}{v[-2:]} (len {len(v)})"


def evidence(name):
    """(value, source, agrees_with_other_copy) for one credential.

    Environment variables take precedence over the config file, so a stale
    `setx` value silently beats an updated file.  `agrees` is None when only
    one copy exists.
    """
    env = os.environ.get(_ENV.get(name, ""), "")
    fileval = _load().get(name.lower(), "")
    value = env or fileval
    src = "environment variable" if env else ("config file" if fileval
                                              else "MISSING")
    agrees = None
    if env and fileval:
        agrees = (env == fileval)
    return value, src, agrees


def explain():
    """Print, per credential: which copy is in use and whether they disagree."""
    print(f"config file(s) searched: {source()}")
    print(f"{'field':10s} {'source':20s} {'value':28s} notes")
    for n in ("app_key", "username", "password", "session"):
        val, src, agrees = evidence(n)
        note = ""
        if agrees is True:
            note = "env and file agree"
        elif agrees is False:
            note = "!! env and file DIFFER - the env value wins"
        print(f"  {n:8s} {src:20s} {mask(val):28s} {note}")
    print("\nIf you changed your Betfair password, update BOTH copies (or clear "
          "the env\none) or the tool will keep sending the old one.")


def source():
    """Which config file is in use (path only - never the contents)."""
    for path in CONFIG_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return "(env vars only)"


if __name__ == "__main__":
    if "--diagnose" in sys.argv:
        explain()
    else:
        print("config source :", source())
        for n in ("app_key", "username", "password", "session"):
            v = get(n)
            print(f"  {n:9s}: {'SET  ' if v else 'not set'} {mask(v)}")
        print("\nRun with --diagnose to see whether an environment variable is "
              "overriding the file.")
