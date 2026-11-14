"""PR Evidence Reviewer: checks PR description claims against code, tests, and CI evidence."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pr-evidence-reviewer")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"
