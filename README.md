# ResInsight development dashboard (prototype)

Static page showing the health of ResInsight `dev`: build matrix, test workflows,
nightly binaries, latest release, code-quality workflows and links.
Plan: https://github.com/magnesj/ResInsight/issues/1208

## How it works

1. `collector/collect.py` (Python stdlib only) queries the GitHub API and writes
   `site/data/status.json`. Each source is isolated; a failing one shows as `unknown`.
2. `site/` is a static page (plain HTML/CSS/JS) that renders `status.json`.
3. `.github/workflows/update-dashboard.yml` runs the collector every 30 min and deploys
   `site/` to GitHub Pages (no data is committed).

## Run locally

```powershell
$env:GITHUB_TOKEN = gh auth token   # optional, raises rate limits
py collector/collect.py
py -m http.server 8000 --directory site   # open http://localhost:8000
```

## Status rules

- Overall **red**: any build workflow failed, or latest build run is older than 36 h.
- **amber**: nightly binaries (`dev-latest`) older than 36 h, or a source is unknown/cancelled.
- Workflows are resolved by file name (see `WORKFLOWS` in `collector/collect.py`).

## Next steps (from the plan)

- Phase 2: gtest XML / pytest JUnit artifacts in OPM/ResInsight, parse into pass/fail counts.
- Phase 3: visual-test report details (diff count, link to report).
- Phase 4: history/trend (`history.json`), notifications.
- Set a `DASHBOARD_TOKEN` secret (read-only PAT) to avoid rate limits and read artifacts.
