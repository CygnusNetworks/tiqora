---
name: tiqora-dual-remote-pull-rebase-drops-commit
description: |
  Tiqora/cygnus repos with a dual-push-URL `origin` (github.com AND
  git.cygnusnet.de) plus a separate `cygnus` remote: after a push race with a
  CI "docs(screenshots) [skip ci]" commit on GitHub, `git pull --rebase` can
  SILENTLY DROP the commit you just made. Use when: (1) push says
  "[rejected] (fetch first)" for GitHub while the cygnusnet URL accepted the
  commit, (2) after `pull --rebase` your change is gone from `git log` and the
  working tree, (3) the next push is rejected "non-fast-forward ... tip of
  your current branch is behind" by git.cygnusnet.de, (4) reflog shows
  "pull --rebase (start)/(finish)" with NO "rebase (pick)" line in between.
author: Claude Code
version: 1.1.0
date: 2026-08-13
---

# Dual-remote push race: pull --rebase drops the fresh commit

## Problem
`origin` has two push URLs (GitHub + git.cygnusnet.de) and there is also a
named `cygnus` remote for the internal server. GitHub CI pushes screenshot
commits (`[skip ci]`) directly to GitHub only. A normal
commit → push → reject(GitHub) → `pull --rebase` cycle can end with the fresh
commit silently dropped locally while it already sits as the tip of
git.cygnusnet.de — the two remotes diverge and every further push fails
somewhere.

## Diagnosis
```bash
git remote -v                      # origin has TWO push URLs — one push hits both
git reflog -8                      # commit exists; pull --rebase finished with no pick
git ls-remote origin refs/heads/main
git ls-remote cygnus refs/heads/main   # differing SHAs = diverged
```
The commit is NOT lost: it is in the reflog and on the cygnusnet remote.

## Recovery (verified)
```bash
git cherry-pick <dropped-sha>          # replay onto the new GitHub tip
# re-run the tests the commit carries — verify before pushing
git fetch cygnus                       # force-with-lease needs a fresh tracking ref
git push origin main                   # GitHub fast-forwards; cygnusnet URL rejects (ignore)
git push --force-with-lease cygnus main  # replaces only your own identical commit
git ls-remote origin refs/heads/main; git ls-remote cygnus refs/heads/main  # must match
```
`--force-with-lease` is safe here because the replaced tip is your own
just-pushed, content-identical commit. The cygnus push re-triggers Jenkins.

## Prevention (verified 2026-08-13, second occurrence)

The race recurs every time a release tag triggers the CI screenshot commit.
Instead of `git pull --rebase` (which dropped the commit), use the explicit
two-step — it kept the commit intact when tested:

```bash
git fetch origin
git rebase origin/main          # explicit upstream, no fork-point heuristic
git log --oneline -1            # VERIFY your commit is still the tip
git push origin main            # GitHub fast-forwards; cygnusnet URL rejects
git fetch cygnus && git push --force-with-lease cygnus main
git ls-remote origin refs/heads/main; git ls-remote cygnus refs/heads/main
```

## Notes
- Root cause of the drop is unconfirmed (fork-point heuristic or the RTK git
  hook are suspects) — after any `pull --rebase` in this repo, verify your
  commit is still in `git log -1` before pushing.
- See also: [[jenkins-git-tag-multi-remote-divergence]] (tags across the same
  remotes), rtk-hook-checkout-b-does-not-switch (RTK hook git anomalies).
