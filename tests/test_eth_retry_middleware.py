"""ETH RPC retry middleware. No live node."""

from __future__ import annotations

import logging
from unittest.mock import patch

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from commands.followers.eth_follower import custom_retry_middleware


def _response_error() -> aiohttp.ClientResponseError:
    request_info = aiohttp.RequestInfo(
        URL("https://rpc.example/secret-key-marker"),
        "POST",
        CIMultiDictProxy(CIMultiDict()),
    )
    return aiohttp.ClientResponseError(
        request_info,
        (),
        status=503,
        message="unavailable",
    )


async def _middleware(errors: list[BaseException], result: str = "ok"):
    attempts = 0

    async def make_request(method: str, params: list) -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= len(errors):
            raise errors[attempts - 1]
        return result

    return await custom_retry_middleware(make_request, None), lambda: attempts


@pytest.mark.asyncio
async def test_timeout_is_retried_then_succeeds():
    middleware, attempts = await _middleware([TimeoutError(), TimeoutError()])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    with patch("commands.followers.eth_follower.asyncio.sleep", new=sleep):
        assert await middleware("eth_blockNumber", []) == "ok"

    assert attempts() == 3
    assert slept == [5, 5]


@pytest.mark.asyncio
async def test_connection_error_is_retried_then_succeeds():
    middleware, attempts = await _middleware([aiohttp.ServerDisconnectedError()])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    with patch("commands.followers.eth_follower.asyncio.sleep", new=sleep):
        assert await middleware("eth_getLogs", []) == "ok"

    assert attempts() == 2
    assert slept == [5]


@pytest.mark.asyncio
async def test_http_status_is_retried_without_logging_url(caplog: pytest.LogCaptureFixture):
    middleware, attempts = await _middleware([_response_error()])

    async def sleep(seconds: float) -> None:
        return None

    with caplog.at_level(logging.ERROR):
        with patch("commands.followers.eth_follower.asyncio.sleep", new=sleep):
            assert await middleware("eth_call", []) == "ok"

    assert attempts() == 2
    text = caplog.text
    assert "503" in text
    assert "unavailable" in text
    assert "secret-key-marker" not in text


@pytest.mark.asyncio
async def test_non_transport_error_is_not_retried():
    middleware, attempts = await _middleware([ValueError("bug")])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    with patch("commands.followers.eth_follower.asyncio.sleep", new=sleep):
        with pytest.raises(ValueError, match="bug"):
            await middleware("eth_call", [])

    assert attempts() == 1
    assert slept == []
