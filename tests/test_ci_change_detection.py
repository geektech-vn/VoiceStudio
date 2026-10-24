"""CI skips the Electron-only jobs for pull requests that provably cannot affect them.

The detection script is extracted from ci.yml and run against a fake `gh`, so the
exact shipped regex is what is tested. The rule is an allowlist: unknown paths run
everything, and any failure to detect also runs everything.
"""
from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def _script() -> str:
    step = next(s for s in WORKFLOW["jobs"]["changes"]["steps"] if s.get("id") == "detect")
    return step["run"]


def _detect(tmp_path: Path, files: str | None, event: str = "pull_request", gh_fails: bool = False) -> str:
    gh = tmp_path / "gh"
    body = "exit 1" if gh_fails else f"cat <<'LIST'\n{files or ''}\nLIST"
    gh.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "output"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out),
               EVENT=event, PR="1", REPO="o/r", GH_TOKEN="x")
    subprocess.run(["bash", "-e", "-c", _script()], env=env, check=True, capture_output=True)
    return out.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("files", [
    "backend/api/routers/dub_export.py\ntests/test_dub_export.py",
    "CHANGELOG.md\ndocs/RELEASING.md\nREADME.md",
    "omnivoice/utils/text.py\nuv.lock\npyproject.toml",
    "scripts/release.py\ntests/backend/api/test_x.py",
    "mobile/android/app/build.gradle.kts\nmobile/contract/openapi.json",
    ".github/ISSUE_TEMPLATE/bug.yml",
    ".github/workflows/docker.yml\n.github/workflows/mobile.yml\nMakefile",
    ".github/scripts/scam_guard.py\n.github/CONTRIBUTING.md",
])
def test_python_docs_and_mobile_only_pull_requests_skip_the_electron_jobs(tmp_path, files):
    assert _detect(tmp_path, files) == "electron=false"


@pytest.mark.parametrize("files", [
    "electron/src/renderer/src/app.tsx",
    "backend/api/x.py\nelectron/src/main/updater.ts",      # one Electron file is enough
    "native/desktop-bridge/src/lib.rs",
    "package.json", "bun.lock", "config/app.json",
    "tests/fixtures/longform_parser_cases.json",            # shared with the JS suite
    "tests/frontend/ssml.test.mjs",
    ".github/workflows/ci.yml", ".github/workflows/electron-release.yml",
    ".github/workflows/electron-build.yml", "backend/x.py\n.github/workflows/ci.yml",
    "scripts/install.sh", "scripts/export-mobile-tokens.ts",
    "something/unknown.txt",                                # unknown paths fail safe
    "docs/logo.svg\nbackend/x.py\nLICENSE",                 # LICENSE is not allowlisted
])
def test_anything_else_runs_the_electron_jobs(tmp_path, files):
    assert _detect(tmp_path, files) == "electron=true"


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule"])
def test_main_pushes_and_manual_runs_always_run_everything(tmp_path, event):
    assert _detect(tmp_path, "docs/only.md", event=event) == "electron=true"


def test_a_failed_or_empty_lookup_runs_everything(tmp_path):
    assert _detect(tmp_path, None, gh_fails=True) == "electron=true"
    assert _detect(tmp_path, "") == "electron=true"


def test_the_electron_jobs_are_gated_on_the_detection_and_survive_its_failure():
    jobs = WORKFLOW["jobs"]
    for name in ("ui-crawler", "linux-native-package", "electron-ui"):
        assert jobs[name]["needs"] == "changes", name
        condition = jobs[name]["if"]
        assert "!cancelled()" in condition and "outputs.electron != 'false'" in condition, name
    # the merge gate always reports, even if detection broke
    assert jobs["test"]["needs"] == "changes" and jobs["test"]["if"] == "${{ !cancelled() }}"
    gated = {s["name"] for s in jobs["test"]["steps"] if "outputs.electron != 'false'" in str(s.get("if", ""))}
    assert gated == {
        "Frontend unit tests", "Electron typecheck, tests and production contract",
        "Snap artifact repair regression", "Install Playwright chromium",
        "Electron renderer workflow smokes",
    }


def test_required_check_names_are_unchanged():
    names = {j["name"] for j in WORKFLOW["jobs"].values()}
    assert {"Tests (backend + frontend)", "UI crawler", "Linux native package",
            "Electron installer contracts"} <= names
    assert any(n.startswith("Desktop UI (") for n in names)
