# UI Spend Report

How much of your engineering team's time and money goes into building and fixing screens.

An agent skill for Claude Code (also works with Codex). Point it at your GitHub organization and it
produces a local, self-contained HTML report with four numbers:

- the share of engineering time spent on the user interface;
- what that costs per month;
- how much of it is maintenance of screens that already exist;
- what that maintenance costs per year.

The Brazilian Portuguese report reads as a short article: a headline that states the main finding, a lede, four estimates, then six numbered sections (what the interface costs, where maintenance concentrates, how it moves month to month, rework within 30 days, the team's position in an anonymous open-source reference base, and the method with a visible diagram). Each chart has a title and a caption. It diagnoses; it does not prescribe actions. Embedded fonts, inline charts and motion work offline; without JavaScript, with reduced motion or in print the page shows its final state.

## Privacy

Everything runs on your machine, with your own `gh` login. The skill reads only pull-request
metadata (titles, labels, file paths, lines added and removed, merge dates), never file contents or
diffs. The report shows aggregates by area and repository only, never per person. No data leaves
your environment.

## Install

```sh
git clone https://github.com/emana-ai/ui-spend-report ~/.claude/skills/ui-spend-report
```

Then ask your agent: *"run the UI spend report for the organization `<org>`"*. It checks your
GitHub CLI login (and walks you through fixing it if needed), then asks for the period (default:
last 6 months), the average monthly cost per developer with charges, the currency and the hours per
month (default 160).

Requirements: Python 3.8+ (standard library only) and the [GitHub CLI](https://cli.github.com/)
logged in with read access to the organization.

## How it works

Each merged pull request gets a score from the size of its effective change. Each developer's
monthly cost is split across the pull requests they merged that month, weighted by score, so a
week of work pushed at once counts as a week. Files are classified by path into interface,
back-end, infrastructure, data, docs and integration tests. Unit tests count with the code they
test. Lockfiles, generated code, translations, bots, automation run from human accounts (translation
syncs, dependency updates) and duplicated pull requests (cherry-picks, backports, release and merge
copies of work already counted) are excluded. Maintenance comes from bug or feature labels when a pull
request has them, otherwise from its title. With fewer than 30 interface pull requests the report
does not publish a maintenance rate (nor any other rate built on fewer than 30 interface pull requests). Numbers come from deterministic scripts: the same inputs give
the same report.

With the same run parameters, add `--findings` to export the evidence before drafting `insights.json` (headline, lede and, per section, a title and one or two evidence sentences). Validate the copy with `python3 scripts/validate_insights.py --out DIR` and rerun the report. The HTML uses deterministic templates when editorial copy is absent or invalid.

Refresh the reference base from one aggregate JSON per software company:

```sh
python3 scripts/benchmark.py runs/*/ui-spend-report-*.json --out assets/benchmark.json
```

Each point carries the interface share, the maintenance share, the fix frequency and the 30-day rework share. A comparison is shown only when the asset has the metric. An optional `--sectors sectors.json` supplies local organization-to-sector labels. Only the labels are published. Keep collection periods and author filters comparable.

Full method: [`references/method.md`](references/method.md). Every rule and default is in
[`scripts/defaults.py`](scripts/defaults.py).

Made by [emana.ai](https://emana.ai).

## License

MIT. See [`LICENSE`](LICENSE).
