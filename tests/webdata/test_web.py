"""The web app against the fixture repo's data. These need `npm ci` in web/ (the CI web job runs
them); without node_modules they are skipped, and the skip says so."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import site_repo

from pothole import sitedata

WEB = Path(__file__).resolve().parents[2] / "web"
needs_node = pytest.mark.skipif(
    not (WEB / "node_modules").exists() or shutil.which("npm") is None,
    reason="web/node_modules is missing: run `npm ci` in web/",
)


def run(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=WEB, capture_output=True, text=True, check=False)


@needs_node
@pytest.mark.parametrize("with_agent", [False, True])
def test_every_route_renders_from_the_fixture_data(tmp_path: Path, with_agent: bool) -> None:
    repo = site_repo.make(tmp_path / "repo", with_agent=with_agent)
    sitedata.build(repo, tmp_path / "out")
    done = run("npx", "--no-install", "tsx", "scripts/check-routes.tsx", str(tmp_path / "out"))
    assert done.returncode == 0, done.stderr + done.stdout
    assert "checked 10 routes" in done.stdout


@needs_node
def test_an_empty_history_renders(tmp_path: Path) -> None:
    repo = site_repo.make(tmp_path / "repo")
    (repo / "data" / "runs.jsonl").unlink()
    sitedata.build(repo, tmp_path / "out")
    done = run("npx", "--no-install", "tsx", "scripts/check-routes.tsx", str(tmp_path / "out"))
    # no successful run: the pages must say so instead of a date, which the checker reports
    assert "no last successful run date" in done.stderr
    assert "TypeError" not in done.stderr


DIST = WEB / "dist"


@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web/dist is missing: run the build")
def test_built_references_all_exist() -> None:
    html = (DIST / "index.html").read_text()
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert refs
    for ref in refs:
        assert ref.startswith("/pothole/"), f"{ref} is not under the site's base path"
        assert (DIST / ref.removeprefix("/pothole/")).is_file(), f"{ref} is not in the build"
    assert "/pothole/#/404" in (DIST / "404.html").read_text()
