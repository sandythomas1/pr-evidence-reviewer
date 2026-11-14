"""Errors the runner reports to the user.

Each error carries a stable `code` for the API envelope and a message written for the person using the
tool. Messages must never contain a token, key, or other secret.
"""


class ReviewerError(Exception):
    """Base class for errors that are safe to show to the user."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidPullRequestUrlError(ReviewerError):
    code = "invalid_pr_url"
    http_status = 422


class GitHubAuthError(ReviewerError):
    code = "github_auth"
    http_status = 401


class GitHubNotFoundError(ReviewerError):
    code = "github_not_found"
    http_status = 404


class GitHubRateLimitedError(ReviewerError):
    code = "github_rate_limited"
    http_status = 429


class GitHubUnavailableError(ReviewerError):
    code = "github_unavailable"
    http_status = 502
