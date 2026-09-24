#!/usr/bin/env python3
"""publish_release_asset.py - publish racing_form.db.gz as a GitHub Release asset.

WHY
---
The database was being committed to git every 2 hours.  Two problems:

  1. **Repo growth.**  A ~29 MB binary in every commit is ~350 MB/day of git history,
     and git keeps every version forever.  The repo was already 227 MB.
  2. **Two writers, one file.**  The laptop pipeline and the cloud Action both commit
     racing_form.db.gz, so their branches diverge and each silently overwrites the
     other's data.  This very nearly wiped a night of settled results on 2026-09-24:
     a laptop commit carried a different database and replaying it would have
     replaced the Action's.

A Release asset fixes both.  It is one slot that gets replaced, not appended to, so
there is no history to bloat and no rebase conflict - the newest upload simply wins.
The download URL is public, so the deployed app needs no credentials to read it:

    https://github.com/<owner>/<repo>/releases/download/db-latest/racing_form.db.gz

WHAT THIS DOES
    1. ensure the release tagged `db-latest` exists (create it if not)
    2. delete the existing racing_form.db.gz asset, if any (GitHub will not accept a
       second asset with the same name)
    3. upload the new one
    4. if --untrack, run `git rm --cached racing_form.db.gz` so git stops carrying it

AUTH
    Needs a token with Contents: write.  In Actions that is `secrets.GITHUB_TOKEN`.
    Without one this exits non-zero without touching anything.

USAGE
    GH_TOKEN=... python publish_release_asset.py
    GH_TOKEN=... python publish_release_asset.py --untrack
    python publish_release_asset.py --check
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GZ = os.path.join(os.path.dirname(HERE), "racing_form.db.gz")

REPO = os.environ.get("GITHUB_REPOSITORY", "Qasim9989/Horse_race")
TAG = "db-latest"
ASSET = "racing_form.db.gz"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


def token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def api(path, tok, method="GET", payload=None, base=API, raw=None, ctype=None):
    """Call the GitHub API.  Returns (status, parsed_body_or_text)."""
    data = None
    if raw is not None:
        data = raw
    elif payload is not None:
        data = json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "publish-release-asset")
    if ctype:
        req.add_header("Content-Type", ctype)
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read().decode("utf-8", "replace")
            if body.strip().startswith(("{", "[")):
                return r.status, json.loads(body)
            return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body).get("message", body)
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return 0, str(e)


def ensure_release(tok):
    """The release tagged db-latest.  Created on first use."""
    status, body = api("/repos/%s/releases/tags/%s" % (REPO, TAG), tok)
    if status == 200 and isinstance(body, dict):
        return body
    if status not in (404,):
        print("  [WARN] could not read the release: %s %s" % (status, body))
    status, body = api("/repos/%s/releases" % REPO, tok, "POST", {
        "tag_name": TAG,
        "name": "Live database",
        "body": ("Rolling copy of racing_form.db.gz, replaced on every publish.\n"
                 "The deployed app downloads this instead of the file living in git,\n"
                 "which keeps the repository from growing ~350 MB a day."),
        "prerelease": False,
    })
    if status in (200, 201):
        print("  created release '%s'" % TAG)
        return body
    if status == 422:
        # already exists - race with another run; re-read
        status2, body2 = api("/repos/%s/releases/tags/%s" % (REPO, TAG), tok)
        if status2 == 200:
            return body2
    print("  [FAIL] could not create the release: %s %s" % (status, body))
    return None


def drop_asset(tok, rel):
    """Remove any asset already named racing_form.db.gz - the name must be unique."""
    status, body = api("/repos/%s/releases/%s/assets" % (REPO, rel["id"]), tok)
    if status != 200 or not isinstance(body, list):
        return 0
    removed = 0
    for a in body:
        if a.get("name") == ASSET:
            s2, b2 = api("/repos/%s/releases/assets/%s" % (REPO, a["id"]), tok, "DELETE")
            if s2 == 204:
                removed += 1
                print("  removed the previous asset (%s)" % a.get("id"))
            else:
                print("  [WARN] could not delete asset %s: %s %s" % (a.get("id"), s2, b2))
    return removed


def upload(tok, rel, path):
    with open(path, "rb") as fh:
        blob = fh.read()
    status, body = api("/repos/%s/releases/%s/assets?name=%s" % (REPO, rel["id"], ASSET),
                       tok, "POST", base=UPLOADS, raw=blob, ctype="application/gzip")
    if status in (200, 201):
        return True, body
    return False, "%s %s" % (status, body)


def untrack():
    """Stop git carrying the .gz.  History is untouched; only future commits stop."""
    r = subprocess.run(["git", "rm", "--cached", "--quiet", ASSET],
                       cwd=os.path.dirname(HERE), capture_output=True, text=True)
    if r.returncode == 0:
        print("  git: stopped tracking %s" % ASSET)
        return True
    print("  git: %s was not tracked (or git unavailable)" % ASSET)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report the current asset without uploading")
    ap.add_argument("--untrack", action="store_true",
                    help="also run `git rm --cached racing_form.db.gz` on success")
    a = ap.parse_args()

    print("=" * 72)
    print("  PUBLISH DB AS A RELEASE ASSET")
    print("=" * 72)
    print("  repo  : %s" % REPO)
    print("  tag   : %s" % TAG)
    print("  asset : %s" % GZ)

    if not os.path.exists(GZ):
        print("  [FAIL] %s not found - pack it first" % GZ)
        return 1
    size = os.path.getsize(GZ)
    print("  local : %.1f MB" % (size / 1048576.0))

    tok = token()
    if not tok:
        print("  [FAIL] no GH_TOKEN / GITHUB_TOKEN - cannot publish")
        return 1

    rel = ensure_release(tok)
    if not rel:
        return 1
    print("  release id %s" % rel["id"])

    status, body = api("/repos/%s/releases/%s/assets" % (REPO, rel["id"]), tok)
    if status == 200 and isinstance(body, list):
        for x in body:
            if x.get("name") == ASSET:
                print("  live  : %.1f MB, uploaded %s"
                      % (x.get("size", 0) / 1048576.0, x.get("updated_at")))
    print("  url   : https://github.com/%s/releases/download/%s/%s" % (REPO, TAG, ASSET))

    if a.check:
        print("\n  --check: nothing uploaded.")
        return 0

    print()
    drop_asset(tok, rel)
    ok, body = upload(tok, rel, GZ)
    if not ok:
        print("  [FAIL] upload failed: %s" % body)
        return 1
    print("  [OK] uploaded %.1f MB as %s" % (size / 1048576.0, ASSET))

    if a.untrack:
        print()
        untrack()
    return 0


if __name__ == "__main__":
    sys.exit(main())
