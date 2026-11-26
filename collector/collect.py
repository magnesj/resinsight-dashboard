#!/usr/bin/env python3
"""Collect ResInsight development status into data/status.json.

Stdlib only. Uses GITHUB_TOKEN / GH_TOKEN if set (higher rate limits).
Every source is collected independently; a failing source yields
{"status": "unknown", "error": ...} instead of aborting the run.
"""

import io
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
MAIN_REPO = "OPM/ResInsight"
BUILDS_REPO = "CeetronSolutions/resinsight-builds"
VISUAL_REPO = "CeetronSolutions/ResInsight-visual-tests"
BRANCH = "dev"
STALE_HOURS = 36

# group -> (display name, workflow file name)
WORKFLOWS = {
    "builds": [
        ("Windows / Ubuntu (build + tests)", "ResInsightWithCache.yml"),
        ("RHEL8 (build + unit tests)", "rhel8-unit-tests.yml"),
        ("macOS", "ResInsightMac.yml"),
    ],
    "tests": [
        ("Application framework unit tests", "AppFwkUnitTest.yml"),
        ("rips vs latest dev binary", "test-latest-rips-with-latest-resinsight.yml"),
    ],
    "quality": [
        ("clang-tidy", "clang-tidy.yml"),
        ("clazy", "clazy-report.yml"),
        ("spell-check", "spell-check.yml"),
        ("broken links", "check-broken-links.yml"),
        ("python linting", "python-linting.yml"),
    ],
}


# Workflows uploading gtest-results-* / pytest-results-* artifacts (OPM/ResInsight#15261)
TEST_RESULT_WORKFLOWS = [
    ("Windows / Ubuntu unit tests + pytest", "ResInsightWithCache.yml"),
    ("RHEL8 unit tests", "rhel8-unit-tests.yml"),
    ("macOS unit tests + pytest", "ResInsightMac.yml"),
    ("Application framework unit tests", "AppFwkUnitTest.yml"),
]
TEST_ARTIFACT_PREFIXES = ("gtest-results-", "pytest-results-")
MAX_FAILED_TESTS = 50


def now():
    return datetime.now(timezone.utc)


def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_hours(value):
    return round((now() - parse_ts(value)).total_seconds() / 3600, 1) if value else None


def get(path, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API}{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "resinsight-dashboard")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download_artifact(repo, artifact_id):
    """Download an artifact zip. Needs a token, even for public repositories."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("artifact download requires GITHUB_TOKEN")
    request = urllib.request.Request(f"{API}/repos/{repo}/actions/artifacts/{artifact_id}/zip")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("User-Agent", "resinsight-dashboard")
    try:
        urllib.request.build_opener(_NoRedirect).open(request, timeout=60)
        raise RuntimeError("expected a redirect to the artifact archive")
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location")
        if exc.code not in (301, 302, 307) or not location:
            raise
    # The archive lives on blob storage and must be fetched without the API token
    with urllib.request.urlopen(location, timeout=120) as response:
        return response.read()


def safe(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - isolate source failures
        return {"status": "unknown", "error": str(exc)}


def status_of(run):
    if run["status"] != "completed":
        return "running"
    return {"success": "success", "failure": "failure", "cancelled": "cancelled"}.get(
        run["conclusion"], run["conclusion"] or "unknown"
    )


def workflow_status(repo, name, workflow_file, branch=BRANCH):
    runs = get(
        f"/repos/{repo}/actions/workflows/{workflow_file}/runs",
        branch=branch,
        per_page=10,
    )["workflow_runs"]
    if not runs:
        return {"name": name, "file": workflow_file, "status": "unknown", "error": "no runs"}

    latest = runs[0]
    # Last finished run decides pass/fail when the newest one is still in progress
    finished = next((r for r in runs if r["status"] == "completed"), None)
    result = {
        "name": name,
        "file": workflow_file,
        "status": status_of(latest),
        "url": latest["html_url"],
        "sha": latest["head_sha"][:8],
        "event": latest["event"],
        "started_at": latest["run_started_at"],
        "updated_at": latest["updated_at"],
        "age_hours": age_hours(latest["updated_at"]),
    }
    if finished:
        result["last_finished"] = {
            "status": status_of(finished),
            "url": finished["html_url"],
            "updated_at": finished["updated_at"],
        }
    return result


def jobs_of(repo, run_url):
    run_id = run_url.rstrip("/").split("/")[-1]
    jobs = get(f"/repos/{repo}/actions/runs/{run_id}/jobs", per_page=100)["jobs"]
    return [
        {
            "name": j["name"],
            "status": "running" if j["status"] != "completed" else (j["conclusion"] or "unknown"),
            "url": j["html_url"],
            "started_at": j["started_at"],
            "completed_at": j["completed_at"],
        }
        for j in jobs
    ]


def collect_workflows(group):
    results = []
    for name, workflow_file in WORKFLOWS[group]:
        entry = safe(workflow_status, MAIN_REPO, name, workflow_file)
        entry.setdefault("name", name)
        entry.setdefault("file", workflow_file)
        if group == "builds" and entry.get("url"):
            entry["jobs"] = safe(jobs_of, MAIN_REPO, entry["url"])
        results.append(entry)
    return results


def parse_test_report(xml_bytes):
    """Count results in a gtest XML or JUnit XML report (the testcase layout is shared)."""
    counts = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "failed_tests": []}
    for case in ET.fromstring(xml_bytes).iter("testcase"):
        counts["total"] += 1
        name = f"{case.get('classname', '')}.{case.get('name', '')}".strip(".")
        if case.find("failure") is not None or case.find("error") is not None:
            counts["failed"] += 1
            counts["failed_tests"].append(name)
        elif (
            case.find("skipped") is not None
            or case.get("status") == "notrun"
            or case.get("result") in ("skipped", "suppressed")
        ):
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts


def parse_artifact(archive_bytes):
    total = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "failed_tests": []}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for member in archive.namelist():
            if member.lower().endswith(".xml"):
                counts = parse_test_report(archive.read(member))
                for key in ("total", "passed", "failed", "skipped"):
                    total[key] += counts[key]
                total["failed_tests"] += counts["failed_tests"]
    return total


def test_report_status(failed, total):
    return "failure" if failed else ("success" if total else "unknown")


def workflow_test_results(repo, name, workflow_file, branch=BRANCH):
    """Parse gtest/pytest XML artifacts from the newest completed run that has any."""
    runs = get(
        f"/repos/{repo}/actions/workflows/{workflow_file}/runs",
        branch=branch,
        per_page=10,
        status="completed",
    )["workflow_runs"]
    for run in runs:
        artifacts = [
            a
            for a in get(f"/repos/{repo}/actions/runs/{run['id']}/artifacts", per_page=100)["artifacts"]
            if a["name"].startswith(TEST_ARTIFACT_PREFIXES) and not a["expired"]
        ]
        if not artifacts:
            continue
        reports = []
        for artifact in sorted(artifacts, key=lambda a: a["name"]):
            report = {"name": artifact["name"]}
            try:
                report.update(parse_artifact(download_artifact(repo, artifact["id"])))
                report["failed_tests"] = report["failed_tests"][:MAX_FAILED_TESTS]
                report["status"] = test_report_status(report["failed"], report["total"])
            except Exception as exc:  # noqa: BLE001 - one bad artifact must not hide the rest
                report.update({"status": "unknown", "error": str(exc)})
            reports.append(report)
        statuses = [r["status"] for r in reports]
        status = "failure" if "failure" in statuses else ("unknown" if "unknown" in statuses else "success")
        return {
            "name": name,
            "file": workflow_file,
            "status": status,
            "url": run["html_url"],
            "sha": run["head_sha"][:8],
            "updated_at": run["updated_at"],
            "age_hours": age_hours(run["updated_at"]),
            "reports": reports,
        }
    return {"name": name, "file": workflow_file, "status": "unknown", "error": "no test artifacts in recent runs"}


def collect_test_results():
    return [safe(workflow_test_results, MAIN_REPO, name, workflow_file) for name, workflow_file in TEST_RESULT_WORKFLOWS]


def collect_head():
    commit = get(f"/repos/{MAIN_REPO}/commits/{BRANCH}")
    return {
        "sha": commit["sha"][:8],
        "message": commit["commit"]["message"].splitlines()[0],
        "author": commit["commit"]["author"]["name"],
        "date": commit["commit"]["author"]["date"],
        "url": commit["html_url"],
    }


def collect_latest_release():
    release = get(f"/repos/{MAIN_REPO}/releases/latest")
    return {
        "tag": release["tag_name"],
        "name": release["name"],
        "published_at": release["published_at"],
        "url": release["html_url"],
    }


def collect_nightly_binaries():
    release = get(f"/repos/{BUILDS_REPO}/releases/tags/dev-latest")
    # dev-latest is a rolling release: assets are replaced nightly while the release
    # itself keeps its original publish date, so freshness is the newest asset upload.
    assets = [
        {
            "name": a["name"],
            "size": a["size"],
            "url": a["browser_download_url"],
            "updated_at": a["updated_at"],
        }
        for a in release["assets"]
    ]
    published = max((a["updated_at"] for a in assets), default=release["published_at"])
    age = age_hours(published)
    return {
        "status": "success" if age is not None and age <= STALE_HOURS else "stale",
        "published_at": published,
        "age_hours": age,
        "url": release["html_url"],
        "assets": assets,
    }


def collect_visual_tests():
    entry = workflow_status(VISUAL_REPO, "Nightly regression", "nightly.yml", branch="main")
    return entry


def overall_status(builds, nightly, test_results=()):
    statuses = [b.get("last_finished", {}).get("status", b["status"]) for b in builds]
    if "failure" in statuses or any(t.get("status") == "failure" for t in test_results):
        return "red"
    if any(b.get("age_hours") is not None and b["age_hours"] > STALE_HOURS for b in builds):
        return "red"
    if nightly.get("status") == "stale" or "unknown" in statuses or "cancelled" in statuses:
        return "amber"
    return "green"


def main(out_path):
    builds = collect_workflows("builds")
    nightly = safe(collect_nightly_binaries)
    status = {
        "generated_at": now().isoformat(timespec="seconds"),
        "branch": BRANCH,
        "head": safe(collect_head),
        "builds": builds,
        "tests": collect_workflows("tests") + [safe(collect_visual_tests)],
        "test_results": collect_test_results(),
        "quality": collect_workflows("quality"),
        "release": safe(collect_latest_release),
        "nightly": nightly,
        "links": [
            {"name": "Latest release", "url": f"https://github.com/{MAIN_REPO}/releases/latest"},
            {"name": "All releases", "url": f"https://github.com/{MAIN_REPO}/releases"},
            {"name": "resinsight.org", "url": "https://resinsight.org"},
            {"name": "Python API (api.resinsight.org)", "url": "https://api.resinsight.org"},
            {"name": "GitHub repository", "url": f"https://github.com/{MAIN_REPO}"},
            {"name": "Issues", "url": f"https://github.com/{MAIN_REPO}/issues"},
            {"name": "Pull requests", "url": f"https://github.com/{MAIN_REPO}/pulls"},
            {"name": "resinsight-builds", "url": f"https://github.com/{BUILDS_REPO}"},
            {"name": "Visual tests", "url": f"https://github.com/{VISUAL_REPO}"},
        ],
    }
    status["overall"] = overall_status(builds, nightly, status["test_results"])

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} (overall: {status['overall']})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "site/data/status.json")
