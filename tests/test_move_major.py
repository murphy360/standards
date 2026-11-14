"""tools/move_major.py: a published release moves its major tag (#10). No network:
the GitHub API is a fake that holds tags, releases and check runs."""

import importlib.util
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("move_major", TOOLS / "move_major.py")
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)

OLD, NEW = "a" * 40, "b" * 40


class FakeApi:
    def __init__(self, tags, releases, checks=None):
        self.tags = dict(tags)  # name -> commit sha
        self.releases = releases  # (tag, prerelease)
        self.checks = (
            checks if checks is not None else {NEW: [("completed", "success")]}
        )
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/releases"):
            return [
                {"tag_name": t, "prerelease": p, "draft": False}
                for t, p in self.releases
            ]
        if path.startswith("/git/ref/tags/"):
            sha = self.tags.get(path.rsplit("/", 1)[1])
            return {"object": {"type": "commit", "sha": sha}} if sha else None
        if path.startswith("/commits/"):
            runs = self.checks.get(path.split("/")[2], [])
            return {"check_runs": [{"status": s, "conclusion": c} for s, c in runs]}
        if method == "PATCH":
            self.tags[path.rsplit("/", 1)[1]] = body["sha"]
        elif method == "POST":
            self.tags[body["ref"].removeprefix("refs/tags/")] = body["sha"]
        return {}


def release(api, tag, prerelease=False, dry_run=False, wait_s=0):
    return mm.run(api, tag, prerelease, wait_s, dry_run)


def test_a_release_moves_its_major_tag():
    api = FakeApi(
        {"v2": OLD, "v2.3.0": OLD, "v2.4.0": NEW}, [("v2.3.0", 0), ("v2.4.0", 0)]
    )
    assert release(api, "v2.4.0") == (0, f"v2 moved from {OLD[:7]} to {NEW[:7]}")
    assert api.tags["v2"] == NEW
    assert ("PATCH", "/git/refs/tags/v2", {"sha": NEW, "force": True}) in api.calls


def test_a_major_release_creates_the_major_tag():
    api = FakeApi({"v2": OLD, "v3.0.0": NEW}, [("v3.0.0", False)])
    assert release(api, "v3.0.0") == (0, f"v3 created to {NEW[:7]}")
    assert api.tags["v3"] == NEW and api.tags["v2"] == OLD


def test_a_tag_already_in_place_is_left_alone():
    api = FakeApi({"v2": NEW, "v2.4.0": NEW}, [("v2.4.0", False)])
    assert release(api, "v2.4.0") == (0, f"v2 is already at {NEW[:7]}")
    assert not [c for c in api.calls if c[0] != "GET"]


@pytest.mark.parametrize(
    ("tag", "prerelease", "why"),
    [
        ("v2.4.0-rc1", False, "v2.4.0-rc1 is not vX.Y.Z: no major tag moves"),
        ("nightly", False, "nightly is not vX.Y.Z: no major tag moves"),
        ("v2.4.0", True, "v2.4.0 is a pre-release: v2 stays"),
    ],
)
def test_only_a_full_release_moves_anything(tag, prerelease, why):
    api = FakeApi({"v2": OLD, tag: NEW}, [(tag, prerelease)])
    assert release(api, tag, prerelease) == (0, why)
    assert api.tags["v2"] == OLD


def test_a_fix_to_an_older_line_does_not_move_the_tag_back():
    api = FakeApi({"v2": OLD, "v2.3.1": NEW}, [("v2.4.0", False), ("v2.3.1", False)])
    assert release(api, "v2.3.1") == (0, "v2.3.1 is older than v2.4.0: v2 stays")
    assert api.tags["v2"] == OLD


def test_a_newer_pre_release_does_not_hold_the_tag_back():
    api = FakeApi({"v2": OLD, "v2.4.0": NEW}, [("v2.5.0", True), ("v2.4.0", False)])
    assert release(api, "v2.4.0")[0] == 0 and api.tags["v2"] == NEW


def test_a_failed_ci_leaves_the_tag_and_fails_the_run():
    api = FakeApi(
        {"v2": OLD, "v2.4.0": NEW},
        [("v2.4.0", False)],
        {NEW: [("completed", "failure")]},
    )
    code, line = release(api, "v2.4.0")
    assert code == 1 and line.startswith("v2 stays: `result` did not pass on bbbbbbb")
    assert api.tags["v2"] == OLD


def test_no_ci_run_at_all_leaves_the_tag():
    api = FakeApi({"v2": OLD, "v2.4.0": NEW}, [("v2.4.0", False)], {})
    code, line = release(api, "v2.4.0")
    assert code == 1 and "no finished `result` run on bbbbbbb after 0 s" in line


def test_a_ci_run_still_going_is_waited_for():
    api = FakeApi({"v2": OLD, "v2.4.0": NEW}, [("v2.4.0", False)])
    replies = [[("in_progress", None)], [("completed", "success")]]
    api.checks = {}
    real = api.call

    def call(method, path, body=None):
        if path.startswith("/commits/"):
            runs = replies.pop(0)
            return {"check_runs": [{"status": s, "conclusion": c} for s, c in runs]}
        return real(method, path, body)

    api.call = call
    slept = []
    assert mm.ci_passed(api, NEW, 60, sleep=slept.append) == (True, "")
    assert slept == [mm.POLL_S]


def test_a_dry_run_changes_nothing():
    api = FakeApi({"v2": OLD, "v2.4.0": NEW}, [("v2.4.0", False)])
    assert release(api, "v2.4.0", dry_run=True) == (
        0,
        f"dry run: v2 would be moved from {OLD[:7]} to {NEW[:7]}",
    )
    assert api.tags["v2"] == OLD


def test_an_annotated_tag_is_followed_to_its_commit():
    api = FakeApi({}, [])

    def call(method, path, body=None):
        if path == "/git/ref/tags/v2.4.0":
            return {"object": {"type": "tag", "sha": "c" * 40}}
        return {"object": {"type": "commit", "sha": NEW}}

    api.call = call
    assert mm.commit_of(api, "v2.4.0") == NEW


def test_the_outcome_goes_to_the_run_summary(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    mm.report("v2 moved")
    assert summary.read_text() == "v2 moved\n"
    assert capsys.readouterr().out == "move_major: v2 moved\n"


def test_without_a_token_it_stops(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="GH_TOKEN is not set"):
        mm.main(["--repo", "o/r", "--tag", "v2.4.0"])
