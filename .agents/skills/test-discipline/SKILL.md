---
name: test-discipline
description: Helps agents design, write, review, and verify tests in modernizer-solution with fewer cross-platform failures and safer reporting. Use when adding or changing tests, fixing failing tests, reviewing test quality, handling Linux/Windows differences, diagnosing baseline regressions, or preparing final verification reports.
---

# Test Discipline Skill

## Purpose

This skill helps agents write, review, and verify tests in `modernizer-solution` with fewer cross-platform failures, less token waste, better performance, and safer reporting. It encodes repo lessons from M2 worker launch and workspace testing, especially Linux/Windows process behavior, pytest isolation, baseline proof, and explicit staging.

## When to use

Use when:
- adding or changing tests;
- fixing failing tests;
- reviewing implementation quality;
- verifying issue completion;
- handling Linux/Windows differences;
- diagnosing baseline vs branch regressions;
- creating final issue reports.

## Sources of truth

Priority:
1. assigned issue and acceptance criteria
2. approved implementation plan / ADR
3. repo code and existing tests
4. AGENTS.md
5. this skill

This skill never overrides issue scope or `AGENTS.md`. When sources conflict, report the conflict instead of silently choosing.

## Fast workflow

1. Check git state.
2. Use Graphify first when available.
3. Run focused baseline tests before editing.
4. Make smallest issue-owned change.
5. Run focused tests.
6. Run affected suite.
7. Run full suite when practical.
8. Compare failures against clean `origin/DEMO2` before calling them unrelated.
9. Stage only explicit files.
10. Final report with exact commands/output.

## Graphify-first test exploration

Run focused Graphify queries before broad scans when `graphify-out/graph.json` exists:

```bash
graphify --version || true
graphify query "Which tests cover <component or behavior>?"
graphify query "Which services call <component>?"
graphify path "<component>" "<test or dependency>"
graphify explain "<component>"
```

Graphify is navigation only, never acceptance proof. Confirm decisions against source, tests, issues, plans, and `AGENTS.md`.

## Test design rules

- Prefer small deterministic tests.
- Test behavior, not implementation trivia.
- Keep domain/application tests portable.
- Keep infrastructure/OS tests isolated and explicitly platform-gated.
- Use fake ports/adapters for portable service tests.
- Use real OS integration tests only for OS behavior.
- Avoid sleeps/time-based flakiness where polling or fake clocks work.
- Use `tmp_path`/`tmp_path_factory`, not shared repo paths.
- Use `sys.executable` when launching current Python in tests.
- Do not hardcode local Python paths such as `C:/Python313/python.exe` in new tests.
- Do not depend on user-specific paths, usernames, temp names, installed Maven path, or symlink privilege.
- Preserve local patterns from tests such as `tests/control_tower/test_m2_worker_launch.py`, `tests/control_tower/test_m2_workspace.py`, and `tests/control_tower/test_artifact_paths.py`.

## Monkeypatch and platform mocking rules

- Never monkeypatch global `os.name`, `pathlib.os.name`, or stdlib platform globals used by pytest/pathlib.
- Do not rely on leaked global state between tests.
- Add project-owned helpers like `_is_windows()` / `is_windows_platform()` and monkeypatch those helpers.
- Use `monkeypatch.context()` for risky scoped patches.
- Patch the narrow dependency the code actually calls.
- For Windows path logic on Linux, use `PureWindowsPath`, not concrete `WindowsPath`.
- For real platform behavior, use real platform tests, not fake global OS mutation.

Why this is strict: a Linux full-suite run crashed when tests patched `os.name = "nt"`, which made pytest/pathlib try to use concrete Windows path behavior on Linux. Use helper indirection instead, as seen in Copilot CLI tests that patch `_is_windows()`.

## Linux rules

- Linux must run portable domain/application/persistence/API tests.
- Windows-only integration tests must skip with explicit reason.
- Linux must not fake Windows process-control support.
- If non-Windows behavior is expected to fail closed, test that.
- If full suite fails, prove branch-caused vs baseline using clean `origin/DEMO2`.
- Use this baseline pattern:

```bash
git worktree add ../modernizer-demo2-baseline origin/DEMO2
cd ../modernizer-demo2-baseline
python -m pytest -q -p no:cacheprovider -rs --tb=short --maxfail=3
```

## Windows rules

- Windows must run Windows-only tests that were skipped on Linux.
- Do not accept skipped Windows Job Object tests on Windows.
- Symlink tests may skip only when privilege is unavailable and the skip reason includes the actual privilege issue.
- Use Windows-safe paths and avoid hardcoded version paths.
- For process launching tests, verify no `shell=True`, controlled env, no browser-controlled args, and handle cleanup.
- For Job Object tests, verify create suspended -> assign to job -> resume, and cleanup on failure.
- Check that `subprocess.CREATE_SUSPENDED` availability assumptions do not hide real Windows behavior; use a known flag value only when the runtime API lacks the constant and the test proves the branch on Windows.
- Verify process and thread handles are closed, Job Object handles are retained or closed intentionally, and failure paths terminate/cleanup the child process.

## Skip/xfail policy

- Skip only for real unavailable platform/capability.
- Skip reason must be explicit and searchable.
- Do not skip to hide product failures.
- Use xfail only for known accepted defects with issue reference.
- Every skip added must be explained in final report.

Good repo examples:
- `Windows-only Job Object integration; skipped on non-Windows.`
- `Windows symlink creation privilege unavailable: <actual exception>`

## Baseline failure policy

- Never call a failure unrelated without evidence.
- Compare against clean `origin/DEMO2`.
- Report exact failing test names, errors, and whether they reproduce on baseline.
- If branch-caused, fix it.
- If baseline, report it separately and do not change unrelated scope unless asked.

## Performance rules

- Start with focused tests.
- Then affected suite.
- Then full suite when practical.
- Use `--maxfail=1` or `--maxfail=3` for diagnosis.
- Use `-q -rs --tb=short` for compact output.
- Do not run expensive real integrations unless env var/issue requires them.
- Avoid broad repo scans; use Graphify and targeted `rg`.

## Required command matrix

Linux:

```bash
python -m pytest tests/control_tower/test_m2_worker_launch.py -q -rs --tb=short
python -m pytest tests/control_tower -q -rs --tb=short
python -m pytest -q -p no:cacheprovider -rs --tb=short
```

Windows:

```powershell
py -m pytest tests/control_tower/test_m2_worker_launch.py -q -rs --tb=short
py -m pytest tests/control_tower -q -rs --tb=short
py -m pytest -q -p no:cacheprovider -rs --tb=short
```

Generic:

```bash
git diff --check
git diff --cached --check
```

## Staging and commit rules

- Run `git status --short` before staging.
- Never use `git add .` when unrelated files exist.
- Stage explicit issue-owned files only.
- Do not commit local config, logs, databases, env files, generated caches, or unrelated work.
- After commit, show `git status --short` and `git log -1 --oneline`.

## Final report template

Include:
- branch/base commit
- changed files
- issue-owned scope
- tests run with exact results
- Linux behavior
- Windows behavior
- skips and reasons
- baseline failures and evidence
- final status
- final git status
- commit hash
- pushed or not pushed

## Pre-submit review checklist

- no global `os.name` patching
- no `pathlib.os.name` patching
- no hardcoded Python executable path
- `sys.executable` for current Python
- `tmp_path`/`tmp_path_factory` over shared paths
- `PureWindowsPath` for Windows path parsing on Linux
- real Windows tests run on Windows
- Windows Job Object tests skip on Linux only, with explicit reason
- symlink privilege skip allowed only with explicit reason
- branch vs baseline failures proven before reporting as unrelated
- explicit path staging only
