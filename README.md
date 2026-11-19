# UI Spend Report

How much of your engineering team's time and money goes into building and fixing screens.

An agent skill for Claude Code (also works with Codex). Point it at your GitHub organization and it
produces a local, self-contained HTML report with four numbers:

- the share of engineering time spent on the user interface;
- what that costs per month;
- how much of it is maintenance of screens that already exist;
- what that maintenance costs per year.

The Brazilian Portuguese report opens with the four estimates, then charts monthly investment, repository concentration and maintenance. The expandable appendix contains the complete breakdown and method. Embedded fonts and inline charts work offline.

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
test. Lockfiles, generated code, translations, bots and duplicated pull requests (cherry-picks,
backports) are excluded. Numbers come from deterministic scripts: the same inputs give the same
report.

Full method: [`references/method.md`](references/method.md). Every rule and default is in
[`scripts/defaults.py`](scripts/defaults.py).

Made by [emana.ai](https://emana.ai).

## License

MIT. See [`LICENSE`](LICENSE).
