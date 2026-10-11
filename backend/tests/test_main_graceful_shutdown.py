"""The API entrypoint must bound uvicorn's graceful shutdown below Docker's 10 s grace."""

from __future__ import annotations

from unittest.mock import patch

from tiqora.main import GRACEFUL_SHUTDOWN_SECONDS, main


def test_api_uses_graceful_shutdown_below_docker_grace() -> None:
    with patch("tiqora.main.uvicorn.run") as run:
        main(["api"])
    assert run.call_args.kwargs["timeout_graceful_shutdown"] == GRACEFUL_SHUTDOWN_SECONDS
    assert GRACEFUL_SHUTDOWN_SECONDS < 10
