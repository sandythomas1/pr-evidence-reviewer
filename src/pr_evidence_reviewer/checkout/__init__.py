"""The repository at a PR's head commit, stored by content and read through one path-safe reader.

Nothing in this package runs repository code, calls git, or names a file on disk after a repository
path. Bytes are verified against the git tree and kept under `checkouts/<key>/blobs/<blob sha>`.
"""
