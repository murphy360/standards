#!/usr/bin/env python3
"""Each agent signs in to GitHub as its own GitHub App, never as the owner.

    python3 agent_app.py register claude      # the link that creates the App
    python3 agent_app.py token                # a one-hour token for the App
    python3 agent_app.py git-identity <slug>  # the commit author for the App

``register`` prints a link to GitHub's "new App" page, filled in with the
permissions an agent needs. The owner opens it, creates the App, generates a
private key and installs the App on the repositories the agent works in.

``token`` reads the App's id and key from ``AGENT_APP_ID`` and
``AGENT_APP_KEY`` (a path to the ``.pem`` file), or from ``--app-id`` and
``--key``, and prints an installation token. It lasts an hour. A session puts it
in ``GH_TOKEN``; ``gh auth setup-git`` then makes git push with it too.

``git-identity`` prints the ``git config`` lines that make commits show the App
as their author.

Standard library and ``openssl`` only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.github.com"
DEFAULT_OWNER = "murphy360"
# What an agent does: push branches, open and merge pull requests, comment on
# issues, edit workflow files, and read CI results. Nothing on the account.
PERMISSIONS = {
    "contents": "write",
    "pull_requests": "write",
    "issues": "write",
    "workflows": "write",
    "actions": "read",
    "checks": "read",
    "statuses": "read",
    "metadata": "read",
}


def register_url(agent: str, owner: str) -> str:
    params = {
        "name": f"{owner}-{agent}",
        "description": f"The {agent} agent's own identity in {owner}'s repositories.",
        "url": f"https://github.com/{owner}",
        "public": "false",
        "webhook_active": "false",
        **PERMISSIONS,
    }
    return "https://github.com/settings/apps/new?" + urllib.parse.urlencode(params)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: str, key_path: str, now: int | None = None) -> str:
    """A JWT signed with the App's private key: GitHub's proof that we are the App."""
    now = int(time.time()) if now is None else now
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    # iat a minute back allows for clock drift; GitHub refuses exp over ten minutes.
    claims = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}
    payload = b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_path],
        input=signing_input,
        capture_output=True,
        check=True,
    ).stdout
    return f"{header}.{payload}.{b64url(signature)}"


def api(method: str, path: str, bearer: str | None = None) -> dict:
    request = urllib.request.Request(f"{API}{path}", method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def installation_token(app_id: str, key_path: str, owner: str) -> str:
    jwt = app_jwt(app_id, key_path)
    installation = api("GET", f"/users/{owner}/installation", jwt)
    created = api("POST", f"/app/installations/{installation['id']}/access_tokens", jwt)
    return created["token"]


def git_identity(slug: str) -> list[str]:
    bot = f"{slug}[bot]"
    user_id = api("GET", f"/users/{urllib.parse.quote(bot)}")["id"]
    return [
        f'git config user.name "{bot}"',
        f'git config user.email "{user_id}+{bot}@users.noreply.github.com"',
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register", help="print the link that creates the App")
    reg.add_argument("agent", help="a short name: claude, grok, codex, jules")
    reg.add_argument("--owner", default=DEFAULT_OWNER)
    tok = sub.add_parser("token", help="print a one-hour installation token")
    tok.add_argument("--app-id", default=os.environ.get("AGENT_APP_ID"))
    tok.add_argument("--key", default=os.environ.get("AGENT_APP_KEY"))
    tok.add_argument("--owner", default=DEFAULT_OWNER)
    ident = sub.add_parser("git-identity", help="print the commit author lines")
    ident.add_argument("slug", help="the App's slug, for example murphy360-claude")
    args = parser.parse_args(argv)

    if args.command == "register":
        print(register_url(args.agent, args.owner))
    elif args.command == "token":
        if not args.app_id or not args.key:
            print("token needs AGENT_APP_ID and AGENT_APP_KEY", file=sys.stderr)
            return 1
        print(installation_token(args.app_id, args.key, args.owner))
    else:
        print("\n".join(git_identity(args.slug)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
