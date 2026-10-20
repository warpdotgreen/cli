"""Dated bypass for the two pinned eth -> xch rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from commands.models import join_message_contents
from commands.policy_runtime import BYPASS_UNTIL, eth_historical_recheck_bypass
from commands.spend_policy import REJECTED_SIG

BRIDGE = bytes.fromhex("208b80e85dac3354dd80f72cc272297909ee81b7")
DESTINATION = bytes.fromhex(
    "c32f398f4fe576bf53a4b405c0308e20e8158c0698607078ed1722933e27f00d"
)
ASSET_ID = bytes.fromhex(
    "51125b1021069ed6a1ed935118dd740fe86d199636edbb3b9ad1bff5344320c5"
)
AMOUNT = bytes.fromhex(
    "0000000000000000000000000000000000000000000000000000000000000063"
)
USDC = bytes.fromhex(
    "000000000000000000000000a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
)
MILLIETH = bytes.fromhex(
    "000000000000000000000000f2d5d8ec69e2faed5eb4de90749c87ee314a4b12"
)
NONCE_EA = bytes.fromhex(
    "00000000000000000000000000000000000000000000000000000000000000ea"
)
NONCE_EB = bytes.fromhex(
    "00000000000000000000000000000000000000000000000000000000000000eb"
)

BEFORE = BYPASS_UNTIL - timedelta(seconds=1)


class _Row:
    def __init__(
        self,
        nonce: bytes,
        contents: tuple[bytes, bytes, bytes],
        *,
        source: bytes | None = None,
        destination: bytes = DESTINATION,
        source_chain: bytes = b"eth",
        destination_chain: bytes = b"xch",
        sig: bytes = b"",
    ):
        self.nonce = nonce
        self.source_chain = source_chain
        self.destination_chain = destination_chain
        self.source = b"\x00" * (64 - len(BRIDGE)) + BRIDGE if source is None else source
        self.destination = destination
        self.contents = join_message_contents(list(contents))
        self.sig = sig


def _ea(**overrides) -> _Row:
    return _Row(NONCE_EA, (USDC, ASSET_ID, AMOUNT), **overrides)


def _eb(**overrides) -> _Row:
    return _Row(NONCE_EB, (MILLIETH, ASSET_ID, AMOUNT), **overrides)


def _flip(value: bytes, index: int = 0) -> bytes:
    flipped = bytearray(value)
    flipped[index] ^= 0x01
    return bytes(flipped)


def test_bypass_deadline_is_2027_11_01_utc():
    assert BYPASS_UNTIL == datetime(2027, 11, 1, tzinfo=timezone.utc)


def test_pinned_rows_match_before_deadline():
    for row in (_ea(), _eb(), _ea(sig=b"c1qqoldportalcoinsig"), _eb(sig=b"c1qqold")):
        assert eth_historical_recheck_bypass(row, BEFORE) is True


def test_each_pinned_field_mismatch_falls_through():
    mismatches = [
        _Row(_flip(NONCE_EA, -1), (USDC, ASSET_ID, AMOUNT)),
        _ea(source=b"\x00" * (64 - len(BRIDGE)) + _flip(BRIDGE, -1)),
        _ea(destination=_flip(DESTINATION, -1)),
        _ea(destination_chain=b"bse"),
        _ea(source_chain=b"bse"),
        _Row(NONCE_EA, (_flip(USDC, -1), ASSET_ID, AMOUNT)),
        _Row(NONCE_EA, (USDC, _flip(ASSET_ID, 0), AMOUNT)),
        _Row(NONCE_EA, (USDC, ASSET_ID, _flip(AMOUNT, -1))),
        _Row(NONCE_EA, (MILLIETH, ASSET_ID, AMOUNT)),
    ]
    for row in mismatches:
        assert eth_historical_recheck_bypass(row, BEFORE) is False


def test_nonzero_source_prefix_falls_through():
    source = bytearray(b"\x00" * (64 - len(BRIDGE)) + BRIDGE)
    source[0] = 0x01
    assert eth_historical_recheck_bypass(_ea(source=bytes(source)), BEFORE) is False


def test_rejected_and_used_sigs_fall_through():
    assert eth_historical_recheck_bypass(_ea(sig=REJECTED_SIG), BEFORE) is False
    assert eth_historical_recheck_bypass(_ea(sig=b"used"), BEFORE) is False
    assert eth_historical_recheck_bypass(_eb(sig=REJECTED_SIG), BEFORE) is False
    assert eth_historical_recheck_bypass(_eb(sig=b"used"), BEFORE) is False


def test_deadline_instant_disables_bypass():
    assert eth_historical_recheck_bypass(_ea(), BYPASS_UNTIL) is False
    assert eth_historical_recheck_bypass(_eb(), BYPASS_UNTIL) is False
