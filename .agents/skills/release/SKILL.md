---
name: release
description: "Ship the wip branch to main — strip WIP: prefixes from commit messages with a scripted git rebase (not the deprecated git filter-branch), fast-forward merge, push, return to wip and force-push. Optional arguments: squash commits, bump the version in pyproject.toml (patch/minor/major or explicit), create an annotated tag, publish a GitHub release. Default invocation is merge-and-push only."
---

# /release — ship wip to main

## When to Use

Invoke `/release` when the `WIP:` commits on `wip` are ready to ship. Default behavior is
conservative: rewrite prefixes, fast-forward merge into `main`, push, return to `wip`.
Everything beyond that (squash, bump, tag, GitHub release) is opt-in via arguments.

## User Instructions

Additional instructions provided with the invocation: $ARGUMENTS

## Argument Parsing

Parse case-insensitively. If ambiguous or self-contradictory, STOP and ask.

- `patch bump` / `minor bump` / `major bump` — bump that component in `pyproject.toml`;
  implies tag.
- `tag` — create the annotated tag (requires a bump or an explicit version).
- `vX.Y.Z` — explicit version, overrides the computed bump.
- `release` — publish a GitHub release (implies tag and bump).
- `squash` — collapse all wip commits into one before merging.
- Anything not covered above (`dry run`, `skip tests`, `publish to PyPI`, `add a remote`)
  → STOP and ask before deviating. PyPI publishing is not part of this skill.

## Safety Principles

- `--force` only ever on `wip` — never on `main`, never on tags.
- Fast-forward merges only (`git merge wip`, never `--no-ff`). Linear history.
- After a successful ship, `main` and `wip` point to the same commit.
- **Never ship a red gate.** If the user insists on shipping a known-red checkpoint, STOP
  and require explicit confirmation before bypassing.
- Confirm the computed version and the release notes with the user **before** tagging or
  publishing.
- Version source of truth: `version = "X.Y.Z"` in `pyproject.toml`; a tag is `v` + that
  value. The CHANGELOG release section must have content before tagging — STOP if empty.
- Remote operations are conditional: if no `origin` exists, do the local merge/tag work
  and report clearly that pushing was skipped — never invent or add a remote.
- `rm` is aliased away in this environment — use `del` for untracked removals; prefer
  `git rm` for tracked files.

## Procedure

### Step 1 — Pre-flight (always)

1. Clean tree: `git status --porcelain` — non-empty → abort.
2. On `wip`: `git branch --show-current`.
3. Sync if a remote exists: `if git remote | grep -q .; then git fetch origin && git fetch --tags; fi`
   — STOP if `main` has diverged from local `main`.
4. Something to ship: `git rev-list --count main..HEAD` — `0` → abort.
5. Ship gate:
   ```bash
   uv sync && uv run pytest -m unit && uv build
   ```
   Non-zero → abort. Run `-m integration` when the environment supports it; report if
   skipped.

### Step 2 — Squash (only if requested)

1. `BASE=$(git merge-base main wip) && git reset --soft "$BASE"`
2. Propose a combined message synthesized from the prior `WIP:` messages (without the
   prefix), confirm with the user, then commit with the standard co-author trailer.
3. Skip Step 3 (no prefixes remain).

### Step 3 — Strip `WIP: ` prefixes (skip if squashed)

Use a scripted rebase — not the deprecated `git filter-branch`:

```bash
GIT_SEQUENCE_EDITOR='sed -i.bak -E "s/^pick/reword/"' \
GIT_EDITOR='sed -i.bak -E "1 s/^WIP: //"' \
git rebase -i main
```

- The sequence editor turns every `pick` into `reword`; the editor strips a leading
  `WIP: ` from each message's first line. Commits without the prefix pass through
  unchanged.
- `sed -i.bak` is the portable (BSD/macOS) form; the `.bak` files live in git's transient
  rebase directory and vanish with it.
- If the rebase stops unexpectedly (a pure message rewrite onto its own base should never
  conflict): `git rebase --abort`, STOP, and report.
- Fallback if the environment refuses even a scripted `rebase -i`:
  ```bash
  uvx git-filter-repo --force --refs main..HEAD \
    --message-callback 'return message[5:] if message.startswith(b"WIP: ") else message'
  ```

### Step 4 — Fast-forward merge into `main`

```bash
git checkout main && git merge wip
```

Never `--no-ff`. If git refuses (non-fast-forward), STOP — `main` has diverged and needs a
human.

### Step 5 — Version bump and tag (only if requested)

1. Read the current version and cross-check the latest tag:
   ```bash
   grep -E '^version = ' pyproject.toml
   git describe --tags --abbrev=0 2>/dev/null || echo "(no tags yet)"
   ```
2. Compute the new version (or use the explicit one); **confirm with the user**.
3. Update `version` in `pyproject.toml`; move the CHANGELOG "Unreleased" section under the
   new version with today's date.
4. Commit on the merged `main`, then tag:
   ```bash
   git commit -am "Release v<version>" -m "Co-Authored-By: <standard agent attribution>"
   git tag -a v<version> -m "Release v<version>"
   ```
   The bump commit lands on `main` before tagging so the tag, `pyproject.toml`, and
   `importlib.metadata.version('sosfs')` all agree; Step 7 carries it back to `wip`.

### Step 6 — Push (only if a remote exists)

```bash
if git remote | grep -q .; then
  git push origin main
  git push origin v<version>   # only if a tag was created
else
  echo "No remote — local refs updated; push skipped."
fi
```

If `release` was requested (requires a remote and `gh`): review prior release style
(`gh release list`, `gh release view <tag>`), draft notes from the CHANGELOG section
(`--notes-file`) or `--generate-notes`, confirm with the user, then
`gh release create v<version> --title "v<version>" ...`.

### Step 7 — Return to `wip` (always; push only if remote)

```bash
git checkout wip
git merge --ff-only main
if git remote | grep -q .; then git push --force origin wip; fi
```

`--ff-only` (rather than `reset --hard`) is self-verifying: it fails loudly if anything
diverged instead of silently discarding it. Afterward `main` and `wip` are identical and
`wip` is ready for the next iteration.

## Examples

```
/release                                  # gate, strip prefixes, ff-merge, push, return to wip
/release minor bump with tag and release  # + version bump, annotated tag, GitHub release
/release squash and patch bump            # one combined commit, patch bump, tag; no GH release
/release tag as v1.0.0 and release        # explicit version instead of a computed bump
```

## Notes

- Verify the result: `git log main -n 50 --format=%s` should show no remaining `WIP: `
  first lines.
- The version is surfaced at runtime via `importlib.metadata.version('sosfs')` — always
  bump the file, not just the tag.
