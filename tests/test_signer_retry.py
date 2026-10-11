"""Signer retry batching. Deadlines stay in memory; no database."""

from __future__ import annotations

import pytest

from commands.policy_runtime import (
    SIGNER_BATCH_CAP,
    SIGNER_RETRY_INITIAL_SECONDS,
    SIGNER_RETRY_MAX_SECONDS,
    SignerRetryDeadlines,
    signer_retry_delay_seconds,
    sign_due_unsigned_messages,
)
from commands.spend_policy import REJECTED_SIG


class _Row:
    def __init__(self, n: int):
        self.source_chain = b"eth"
        self.nonce = n.to_bytes(32, "big")
        self.sig = b""


@pytest.mark.asyncio
async def test_signer_pass_caps_batch_and_skips_until_deadline():
    messages = [_Row(n) for n in range(SIGNER_BATCH_CAP + 4)]
    deadlines = SignerRetryDeadlines()
    attempted: list[_Row] = []

    async def sign_retry(message: _Row):
        attempted.append(message)

    now = 10_000.0
    await sign_due_unsigned_messages(messages, deadlines, sign_retry, now)
    assert len(attempted) == SIGNER_BATCH_CAP
    first_wave = list(attempted)

    attempted.clear()
    await sign_due_unsigned_messages(messages, deadlines, sign_retry, now)
    assert len(attempted) == 4
    assert not set(map(id, first_wave)) & set(map(id, attempted))

    retried = first_wave[0]
    attempted.clear()
    await sign_due_unsigned_messages(
        [retried],
        deadlines,
        sign_retry,
        now + SIGNER_RETRY_INITIAL_SECONDS - 0.001,
    )
    assert attempted == []

    await sign_due_unsigned_messages(
        [retried],
        deadlines,
        sign_retry,
        now + SIGNER_RETRY_INITIAL_SECONDS,
    )
    assert attempted == [retried]

    second_at = now + SIGNER_RETRY_INITIAL_SECONDS
    attempted.clear()
    await sign_due_unsigned_messages(
        [retried],
        deadlines,
        sign_retry,
        second_at + SIGNER_RETRY_INITIAL_SECONDS,
    )
    assert attempted == []
    await sign_due_unsigned_messages(
        [retried],
        deadlines,
        sign_retry,
        second_at + signer_retry_delay_seconds(2),
    )
    assert attempted == [retried]

    fresh = SignerRetryDeadlines()
    attempted.clear()
    await sign_due_unsigned_messages([retried], fresh, sign_retry, now)
    assert attempted == [retried]


@pytest.mark.asyncio
async def test_reject_leaves_the_unsigned_set():
    message = _Row(1)
    deadlines = SignerRetryDeadlines()

    async def sign_reject(row: _Row):
        row.sig = REJECTED_SIG

    await sign_due_unsigned_messages([message], deadlines, sign_reject, 0.0)
    assert message.sig == REJECTED_SIG
    unsigned = [row for row in [message] if row.sig == b""]
    assert unsigned == []

    attempted: list[_Row] = []

    async def sign_again(row: _Row):
        attempted.append(row)

    await sign_due_unsigned_messages(unsigned, deadlines, sign_again, 0.0)
    assert attempted == []


def test_retry_delay_starts_at_a_few_seconds_and_caps_at_five_minutes():
    assert signer_retry_delay_seconds(1) == SIGNER_RETRY_INITIAL_SECONDS
    assert SIGNER_RETRY_INITIAL_SECONDS < 30
    assert signer_retry_delay_seconds(2) == SIGNER_RETRY_INITIAL_SECONDS * 2
    assert signer_retry_delay_seconds(20) == SIGNER_RETRY_MAX_SECONDS
    assert SIGNER_RETRY_MAX_SECONDS == 300
