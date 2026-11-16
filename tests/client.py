"""Test client that addresses the runner the way a browser on this machine does."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

LOCAL_BASE_URL = "http://127.0.0.1:8765"


def local_client(app: FastAPI, **kwargs: Any) -> TestClient:
    return TestClient(app, base_url=LOCAL_BASE_URL, **kwargs)
