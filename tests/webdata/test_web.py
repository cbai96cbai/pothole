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
    assert "checked 11 routes" in done.stdout


@needs_node
def test_an_empty_history_renders(tmp_path: Path) -> None:
    repo = site_repo.make(tmp_path / "repo")
    (repo / "data" / "runs.jsonl").unlink()
    sitedata.build(repo, tmp_path / "out")
    done = run("npx", "--no-install", "tsx", "scripts/render-page.tsx", str(tmp_path / "out"), "#/")
    assert done.returncode == 0, done.stderr
    assert "No successful run yet" in done.stdout and "No runs recorded yet" in done.stdout


def repairs_text(tmp_path: Path, **kw: bool) -> str:
    repo = site_repo.make(tmp_path / "repo", **kw)
    sitedata.build(repo, tmp_path / "out")
    done = run(
        "npx", "--no-install", "tsx", "scripts/render-page.tsx", str(tmp_path / "out"), "#/repairs"
    )
    assert done.returncode == 0, done.stderr
    return " ".join(done.stdout.split())


@needs_node
def test_the_repairs_page_separates_who_decided_each_score(tmp_path: Path) -> None:
    text = repairs_text(tmp_path, with_agent=True)
    for phrase in (
        "Agent run 1",
        "Agent run 2",
        "Decided by the repairer",
        "Cases it decided",
        "Decided by the fixed volume rule, no AI",
        "none (all decided by the fixed rule)",
        "a fixed rule decides, not either repairer, so those cases are scored separately",
        "The checks noticed the planted fault in 27 of 27 fault cases "
        "and flagged 3 of 3 harmless changes.",
        "Repairs that worked, agent minus rule-based fixer: +42 points "
        "(95% interval \u221217 to +100). "
        "The interval resamples whole fault families; this split has only 2 repairable families, "
        "so it is wide.",
        "5 of 6",
        "+ rule 4 of 4",
    ):
        assert phrase in text, phrase
    assert "Agent run 3" not in text and "Rule-based fixer run" not in text
    assert text.index("Agent run 1") < text.index("Agent run 2")
    assert "Only the rule-based fixer is shown" not in text


@needs_node
def test_the_repairs_page_without_an_agent_says_only_the_fixer_is_shown(tmp_path: Path) -> None:
    text = repairs_text(tmp_path)
    assert "Only the rule-based fixer is shown" in text
    assert "agent minus rule-based fixer" not in text


@needs_node
def test_the_repairs_page_with_nothing_published_says_so(tmp_path: Path) -> None:
    repo = site_repo.make(tmp_path / "repo")
    (repo / "results" / "published.json").unlink()
    sitedata.build(repo, tmp_path / "out")
    done = run(
        "npx", "--no-install", "tsx", "scripts/render-page.tsx", str(tmp_path / "out"), "#/repairs"
    )
    assert "No measurement is published yet." in " ".join(done.stdout.split())
    assert "Only the rule-based fixer is shown" not in done.stdout


# figures that must come from a data file, not from the pages' source
LITERALS = [
    r"\b(90|25|59|31)\b",
    r"\$\d",
    r"\b0\.(25|008|128|129)\b",
    r"\b2\.00\b",
    r"\b(two|three|thirty-first)\b",
]


def test_no_page_source_holds_a_figure_that_belongs_in_a_data_file() -> None:
    hits = []
    for path in sorted((WEB / "src").rglob("*.ts*")):
        if "data" in path.relative_to(WEB / "src").parts[:1]:
            continue
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            code = "" if line.lstrip().startswith(("//", "*", "/*")) else line
            for pattern in LITERALS:
                if re.search(pattern, code, flags=re.IGNORECASE):
                    hits.append(f"{path.name}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


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


@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web/dist is missing: run the build")
def test_the_map_worker_files_are_in_the_build() -> None:
    for name in ("maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"):
        assert (DIST / "maplibre" / name).is_file(), f"{name} is missing: the map cannot start"
