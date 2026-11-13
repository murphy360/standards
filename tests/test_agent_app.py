"""tools/agent_app.py: an agent's own GitHub App. No test calls GitHub."""

import base64
import importlib.util
import json
import subprocess
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "agent_app", ROOT / "tools" / "agent_app.py"
)
agent_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent_app)


def unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_register_link_asks_for_what_an_agent_needs_and_no_more():
    url = agent_app.register_url("claude", "murphy360")
    assert url.startswith("https://github.com/settings/apps/new?")
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert params["name"] == "murphy360-claude"
    assert params["public"] == "false"
    assert params["webhook_active"] == "false"
    assert params["contents"] == "write"
    assert params["pull_requests"] == "write"
    assert params["workflows"] == "write"
    assert "administration" not in params  # never the repository's settings


def test_jwt_is_signed_by_the_key_and_short_lived(tmp_path):
    key = tmp_path / "app.pem"
    pub = tmp_path / "app.pub"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(key), "2048"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(pub)],
        check=True,
        capture_output=True,
    )
    header, payload, signature = agent_app.app_jwt("123", str(key), now=1000).split(".")
    assert json.loads(unb64(header)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(unb64(payload)) == {"iat": 940, "exp": 1540, "iss": "123"}
    sig = tmp_path / "sig"
    sig.write_bytes(unb64(signature))
    verify = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig)],
        input=f"{header}.{payload}".encode(),
        capture_output=True,
    )
    assert verify.returncode == 0, verify.stderr


def test_token_finds_the_owner_installation_then_asks_for_a_token(monkeypatch):
    calls = []

    def fake_api(method, path, bearer=None):
        calls.append((method, path, bearer))
        if path == "/users/murphy360/installation":
            return {"id": 77}
        return {"token": "ghs_example"}

    monkeypatch.setattr(agent_app, "app_jwt", lambda app_id, key: "jwt")
    monkeypatch.setattr(agent_app, "api", fake_api)
    assert agent_app.installation_token("1", "k.pem", "murphy360") == "ghs_example"
    assert calls == [
        ("GET", "/users/murphy360/installation", "jwt"),
        ("POST", "/app/installations/77/access_tokens", "jwt"),
    ]


def test_git_identity_uses_the_bot_user_id(monkeypatch):
    monkeypatch.setattr(agent_app, "api", lambda method, path: {"id": 555})
    assert agent_app.git_identity("murphy360-claude") == [
        'git config user.name "murphy360-claude[bot]"',
        'git config user.email "555+murphy360-claude[bot]@users.noreply.github.com"',
    ]


def test_token_without_an_app_says_what_it_needs(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_APP_ID", raising=False)
    monkeypatch.delenv("AGENT_APP_KEY", raising=False)
    assert agent_app.main(["token"]) == 1
    assert "AGENT_APP_ID" in capsys.readouterr().err
