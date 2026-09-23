#!/usr/bin/env python3
"""cloud_trigger.py - let the app's "Settle" button start the GitHub Action.

WHY THIS EXISTS
---------------
The Settle button used to try to settle in the Streamlit process.  It can never work
there, for two reasons:

  1. its result source was a SQL Server database (`(localdb)\\MSSQLLocalDB`) that only
     exists on the archive machine - Streamlit Cloud has no SQL Server and no pyodbc,
     so it settled nothing;
  2. even had it settled, Streamlit Cloud has an EPHEMERAL filesystem and no write
     access to this repository, so the change could not be saved or published.  The
     next container restart would wipe it and every row would go back to
     "\u23f3 Running Today".

The GitHub Action can do both - it has a real disk and write access to the repo - so
the button's job is to *start* it.  This module makes the one API call that does that.

CONFIGURATION (Streamlit Cloud -> App Settings -> Secrets)
    GITHUB_TOKEN = "ghp_..."        # a fine-grained token with Actions: read+write
    GITHUB_REPO  = "owner/repo"     # optional; defaults to DEFAULT_REPO below

A classic token needs the `workflow` scope.  Without a token the button falls back to
explaining the situation rather than pretending to work.
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_REPO = "Qasim9989/Horse_race"
WORKFLOW = "cloud-update.yml"
BRANCH = "main"
API = "https://api.github.com"


def config():
    """Return (token, repo) from st.secrets if available, else the environment."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", DEFAULT_REPO)
    try:
        import streamlit as st
        if "GITHUB_TOKEN" in st.secrets:
            token = str(st.secrets["GITHUB_TOKEN"]).strip()
        if "GITHUB_REPO" in st.secrets:
            repo = str(st.secrets["GITHUB_REPO"]).strip()
    except Exception:
        pass                       # running outside Streamlit, or no secrets file
    return token.strip(), (repo or DEFAULT_REPO).strip()


def _api(path, token, payload=None, method=None):
    """Call the GitHub API.  Returns (status, parsed_or_text)."""
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "racing-form-settle-button")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body).get("message", body)
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return 0, str(e)


def is_configured():
    """True when a token is present, so the button can do something useful."""
    return bool(config()[0])


def trigger(date=None):
    """Start the cloud-update workflow.  Returns (ok, human_message)."""
    token, repo = config()
    if not token:
        return False, (
            "No `GITHUB_TOKEN` configured, so this button cannot start the updater.\n\n"
            "Add it in **Streamlit Cloud \u2192 App Settings \u2192 Secrets**:\n"
            "```toml\nGITHUB_TOKEN = \"ghp_...\"\n```\n"
            "A classic token needs the `workflow` scope; a fine-grained token needs "
            "**Actions: read and write** on this repository.")

    inputs = {"days": "4"}
    if date and date not in ("ALL", ""):
        inputs = {"date": str(date)}

    status, body = _api("/repos/%s/actions/workflows/%s/dispatches" % (repo, WORKFLOW),
                        token, {"ref": BRANCH, "inputs": inputs})

    if status == 204:
        what = ("**%s**" % date) if "date" in inputs else "the last few days"
        return True, (
            f"Updater started for {what}.\n\n"
            "It runs on GitHub's servers \u2014 fetching results, settling the ledger and "
            "committing the result \u2014 and takes about a minute. Streamlit then "
            "redeploys with the updated file, so **check back in 1\u20132 minutes**.")

    if status == 401:
        return False, "GitHub rejected the token (`401`). It is wrong, expired or revoked."
    if status == 403:
        return False, ("GitHub refused (`403`). The token is valid but lacks permission \u2014 "
                       "give it Actions: read and write (classic tokens need the `workflow` scope).")
    if status == 404:
        return False, (f"`404` \u2014 `{repo}` or `{WORKFLOW}` not found. Check `GITHUB_REPO` "
                       "and that the workflow is on the default branch.")
    if status == 422:
        return False, f"`422` \u2014 GitHub rejected the request: {body}"
    return False, f"GitHub returned `{status}`: {body}"


def latest_run():
    """The most recent run of the workflow, for a status line.  None if unavailable."""
    token, repo = config()
    status, body = _api("/repos/%s/actions/workflows/%s/runs?per_page=1" % (repo, WORKFLOW),
                        token)
    if status == 200 and isinstance(body, dict):
        runs = body.get("workflow_runs") or []
        if runs:
            r = runs[0]
            return {"status": r.get("status"), "conclusion": r.get("conclusion"),
                    "created_at": r.get("created_at"), "url": r.get("html_url"),
                    "event": r.get("event")}
    return None
