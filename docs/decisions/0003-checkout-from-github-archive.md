# 0003 — Check out the head commit from GitHub's archive, not with git

- **Status:** Accepted
- **Date:** 2026-11-16
- **Deciders:** Sandy Thomas (owner)

## Context

Until now a review saw only the pull request's diff and its CI check results. Many claims can't be
checked from the diff alone: "the retry helper is used by every upload path" needs a search of the
repository, and "this is covered by tests" needs the tests that exist for the changed files. The
review needs the repository's files at the pull request's head commit, read locally, without running
anything from the repository (ADR 0001 §6).

The obvious tool is `git`. Fetching a pull request's code also means handling author-controlled
content: file names, file contents and archive members, from any repository the person reviews.

## Decision

The runner reads the head commit through the GitHub REST API and stores it as a verified,
content-addressed blob store (`checkout/`, plan D1 and D2).

- **Tree first.** `GET /repos/{owner}/{repo}/git/trees/{head}?recursive=1` gives every path, mode,
  blob SHA and size. Size and file-count limits are checked before anything is downloaded. A
  truncated listing means the repository is too large to read in full.
- **Tarball, verified.** `GET …/tarball/{head}` must answer with a redirect to
  `codeload.github.com`, which is fetched by a separate client that sends no Authorization header.
  The redirect carries a short-lived token, so it is never logged or put in an error (threat T3).
  The archive streams to a temporary file under a compressed-size cap and is read as a stream under
  a decompressed-size cap (gzip bombs, T6). `extract` is never called.
- **Stored by blob SHA.** Each member is hashed as a git blob and kept only if it matches the tree,
  under `blobs/<sha>`. No name on disk ever comes from the repository (T2). Files the archive leaves
  out (`export-ignore`) or changes (`export-subst`, `eol`) are fetched one by one from
  `GET …/git/blobs/{sha}` and verified again, up to 300 per checkout.
- **Cache.** Checkouts live in the per-user cache folder, keyed by a hash of the repository and the
  commit, with an LRU size limit (2 GiB by default) and a 7-day TTL that runs while `prev` runs.
  `prev cache clear [--packets]` deletes them at once.
- **Fails soft.** If the checkout can't be fetched or read, the review falls back to the diff and CI
  checks, says so as an evidence gap, and marks the evidence incomplete. It never fails the review
  and is never read as approval.

*Rejected: `git`* (a bare partial fetch, then `ls-tree`, `cat-file` and `git grep`), for four
reasons:

1. **A required system install.** `gh` doesn't bring git, and on Windows git often lives only inside
   GitHub Desktop, off PATH. The runner would gain a prerequisite it doesn't have today.
2. **The machine-config surface (T1).** System and global gitconfig, credential helpers, templates
   and `GIT_*` environment variables need about 25 overrides to neutralise, and missing one can mean
   code execution or a leaked token.
3. **Tests that skip the real path.** Hermetic tests would need a test-only `file://` remote, which
   bypasses the production HTTPS and credential configuration the tests should prove.
4. **`git grep` fails on a partial clone.** With `--filter=blob:limit` and `GIT_NO_LAZY_FETCH=1` it
   exits 128 with no hits (git 2.56.0), so search would need our own reader anyway.

*Rejected: writing a real working tree now.* It brings back the file-name threats (case folding,
`MAX_PATH`, 8.3 short names, alternate data streams, reserved names). Deep review (#23) will
materialise one from the verified store with a dedicated writer.

## Consequences

- #12's git-specific acceptance criteria (hooks, LFS filters, submodules, fsmonitor) hold by
  construction: there is no git, no `.git/` and no subprocess.
- The base commit is never fetched. Diffs come from the pull request files API and every code
  citation refers to the head commit.
- A repository whose tree listing is truncated, or that is over the size limits, is reviewed from
  the diff and CI checks only, with a gap that says so.
- **Disclosed residual (T8).** A repository's `export-ignore` rules can leave files out of the
  archive. Past the 300-file refetch cap those files aren't read; each is named in an evidence gap
  and the evidence is marked incomplete, so a supported verdict carries a warning.
- Private source code is kept on disk: checkouts in the per-user cache folder (owner-only, LRU plus
  7-day TTL while `prev` runs) and stored evidence packets in the per-user data folder until they are
  cleared with `prev cache clear --packets`.
- The first review of a commit downloads the repository and can take minutes for a large one; later
  reviews of the same commit reuse the checkout.
