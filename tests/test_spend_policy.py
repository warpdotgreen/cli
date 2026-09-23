"""Pure spend-policy tests: no full node, no network."""

from __future__ import annotations

from typing import List, Optional, Tuple
from unittest.mock import patch

import pytest
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.serialized_program import SerializedProgram
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.wallet.cat_wallet.cat_utils import (
    CAT_MOD,
    SpendableCAT,
    construct_cat_puzzle,
    unsigned_spend_bundle_for_spendable_cats,
)
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.trading.offer import OFFER_MOD, OFFER_MOD_HASH

from commands import spend_policy
from commands.spend_policy import (
    BridgeRoutes,
    compute_bridge_tip,
    evm_address_from_bytes32,
    left_pad_32,
    load_wrapped_cat_artifact_bytecode,
    validate_chia_cat_wrap,
    validate_chia_erc20_unwrap,
    validate_evm_erc20_wrap,
)
from drivers.portal import BRIDGING_PUZZLE_HASH
from drivers.utils import raw_hash
from drivers.wrapped_assets import (
    get_burn_inner_puzzle_solution,
    get_cat_burn_inner_puzzle,
    get_cat_burner_puzzle,
    get_cat_burner_puzzle_solution,
    get_cat_minter_puzzle,
    get_wrapped_tail,
)
from drivers.wrapped_cats import (
    get_locker_puzzle,
    get_locker_solution,
    get_p2_controller_puzzle_hash_inner_puzzle_hash,
    get_unlocker_puzzle,
)

PORTAL = bytes32(b"\x01" * 32)
ETH_BRIDGE = bytes.fromhex("22" * 20)
ETH_PORTAL = bytes.fromhex("11" * 20)
MILLIETH = bytes.fromhex("33" * 20)
BSE_BRIDGE = bytes.fromhex("66" * 20)
WRAPPED_CAT_ADDR = bytes.fromhex("99" * 20)
ASSET_A = left_pad_32(bytes.fromhex("aa" * 20))
ASSET_B = left_pad_32(bytes.fromhex("cc" * 20))
RECEIVER = bytes.fromhex("bb" * 20)
DEST_CHAIN = b"eth"
CAT_AMOUNT = 10_000
BURNER_AMOUNT = 1
BRIDGING_TOLL = 10**9
ASSET_AMOUNT = 1_337_000
CHANGE_AMOUNT = 10_000

Q_TAIL = Program.from_bytes(bytes.fromhex("ff0180"))


def _routes() -> BridgeRoutes:
    return BridgeRoutes.from_config(
        {
            "xch": {"portal_launcher_id": PORTAL.hex()},
            "eth": {
                "portal_address": "0x" + ETH_PORTAL.hex(),
                "erc20_bridge_address": "0x" + ETH_BRIDGE.hex(),
                "millieth_address": "0x" + MILLIETH.hex(),
                "portal_impl_codehash": "0x" + "44" * 32,
            },
            "bse": {
                "portal_address": "0x" + "55" * 20,
                "erc20_bridge_address": "0x" + BSE_BRIDGE.hex(),
                "millieth_address": "0x" + "77" * 20,
                "portal_impl_codehash": "0x" + "88" * 32,
            },
        }
    )


def _coin_spend(coin: Coin, puzzle: Program, solution: Program) -> CoinSpend:
    return CoinSpend(
        coin,
        SerializedProgram.from_bytes(bytes(puzzle)),
        SerializedProgram.from_bytes(bytes(solution)),
    )


def _build_erc20_burn(
    *,
    asset: bytes = ASSET_A,
    memo_asset: Optional[bytes] = None,
    tail: Optional[Program] = None,
    tail_hash_for_burner: Optional[bytes32] = None,
    dest_chain: bytes = DEST_CHAIN,
    dest_addr: bytes = ETH_BRIDGE,
    include_cat: bool = True,
    burner_puzzle: Optional[Program] = None,
) -> Tuple[Coin, CoinSpend, Optional[CoinSpend]]:
    """Build burner + CAT melt CoinSpends and the bridging coin they create."""
    memo_asset = asset if memo_asset is None else memo_asset
    burner_puzzle = burner_puzzle or get_cat_burner_puzzle(dest_chain, dest_addr)
    burner_parent = bytes32(b"\x02" * 32)
    burner_coin = Coin(burner_parent, burner_puzzle.get_tree_hash(), BURNER_AMOUNT)

    if tail is None:
        tail = get_wrapped_tail(PORTAL, dest_chain, dest_addr, asset)
    tail_hash = tail.get_tree_hash()
    if tail_hash_for_burner is None:
        tail_hash_for_burner = (
            get_wrapped_tail(PORTAL, dest_chain, dest_addr, memo_asset).get_tree_hash()
            if memo_asset != asset
            else tail_hash
        )

    inner = get_cat_burn_inner_puzzle(
        dest_chain, dest_addr, asset, RECEIVER, BURNER_AMOUNT
    )
    cat_puzzle = construct_cat_puzzle(CAT_MOD, tail_hash, inner, CAT_MOD_HASH)
    cat_parent = bytes32(b"\x03" * 32)
    cat_coin = Coin(cat_parent, cat_puzzle.get_tree_hash(), CAT_AMOUNT)

    cat_spend: Optional[CoinSpend] = None
    if include_cat:
        inner_sol = get_burn_inner_puzzle_solution(
            burner_parent, cat_coin.name(), tail
        )
        spendable = SpendableCAT(
            cat_coin,
            tail_hash,
            inner,
            inner_sol,
            lineage_proof=LineageProof(
                bytes32(b"\x04" * 32), bytes32(b"\x05" * 32), CAT_AMOUNT
            ),
            extra_delta=-CAT_AMOUNT,
            limitations_program_reveal=tail,
            limitations_solution=Program.to(
                (
                    raw_hash([b"\x01", RECEIVER]),
                    raw_hash([b"\x01", bytes([BURNER_AMOUNT])]),
                )
            ),
        )
        cat_spend = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, [spendable]
        ).coin_spends[0]

    burner_sol = get_cat_burner_puzzle_solution(
        cat_parent,
        tail_hash_for_burner,
        CAT_AMOUNT,
        memo_asset,
        RECEIVER,
        burner_coin,
    )
    burner_spend = _coin_spend(burner_coin, burner_puzzle, burner_sol)
    bridging = Coin(burner_coin.name(), BRIDGING_PUZZLE_HASH, BURNER_AMOUNT)
    return bridging, burner_spend, cat_spend


def _vault_inner_ph(asset_id: Optional[bytes32]) -> bytes32:
    return get_p2_controller_puzzle_hash_inner_puzzle_hash(
        get_unlocker_puzzle(DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id).get_tree_hash()
    ).get_tree_hash()


def _build_lock(
    *,
    asset_id: Optional[bytes32],
    with_change: bool = False,
    omit_settlement: bool = False,
    empty_settlement_payments: bool = False,
    spend_vault: bool = False,
) -> Tuple[Coin, CoinSpend, List[CoinSpend]]:
    toll = BRIDGING_TOLL
    locker_puzzle = get_locker_puzzle(DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id)
    locker_parent = bytes32(b"\x10" * 32)
    locker_coin = Coin(locker_parent, locker_puzzle.get_tree_hash(), toll)
    locker_sol = get_locker_solution(
        toll, locker_coin.name(), ASSET_AMOUNT, RECEIVER
    )
    locker_spend = _coin_spend(locker_coin, locker_puzzle, locker_sol)
    bridging = Coin(locker_coin.name(), BRIDGING_PUZZLE_HASH, toll)

    vault_inner_ph = _vault_inner_ph(asset_id)
    other: List[CoinSpend] = []
    if omit_settlement:
        return bridging, locker_spend, other

    offer_parent = bytes32(b"\x11" * 32)
    if empty_settlement_payments:
        payments: list = []
    elif with_change and asset_id is not None:
        change_ph = bytes32(b"\xcd" * 32)
        payments = [
            [locker_coin.name(), [vault_inner_ph, ASSET_AMOUNT]],
            [bytes32(b"\xee" * 32), [change_ph, CHANGE_AMOUNT]],
        ]
    else:
        payments = [[locker_coin.name(), [vault_inner_ph, ASSET_AMOUNT]]]

    if asset_id is None:
        offer_coin = Coin(
            offer_parent,
            OFFER_MOD_HASH,
            ASSET_AMOUNT if payments else 1,
        )
        settlement = _coin_spend(offer_coin, OFFER_MOD, Program.to(payments))
    else:
        total = ASSET_AMOUNT + (CHANGE_AMOUNT if with_change else 0)
        if empty_settlement_payments:
            total = max(total, 1)
        cat_offer_puzzle = construct_cat_puzzle(
            CAT_MOD, asset_id, OFFER_MOD, CAT_MOD_HASH
        )
        cat_offer_coin = Coin(
            offer_parent, cat_offer_puzzle.get_tree_hash(), total
        )
        spendable = SpendableCAT(
            cat_offer_coin,
            asset_id,
            OFFER_MOD,
            Program.to(payments),
            lineage_proof=LineageProof(
                bytes32(b"\x04" * 32), OFFER_MOD_HASH, total
            ),
        )
        settlement = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, [spendable]
        ).coin_spends[0]
    other.append(settlement)

    if spend_vault and payments:
        if asset_id is None:
            vault_ph = vault_inner_ph
            vault_puzzle: Program = get_p2_controller_puzzle_hash_inner_puzzle_hash(
                get_unlocker_puzzle(
                    DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id
                ).get_tree_hash()
            )
        else:
            vault_inner = get_p2_controller_puzzle_hash_inner_puzzle_hash(
                get_unlocker_puzzle(
                    DEST_CHAIN, WRAPPED_CAT_ADDR, PORTAL, asset_id
                ).get_tree_hash()
            )
            vault_puzzle = construct_cat_puzzle(
                CAT_MOD, asset_id, vault_inner, CAT_MOD_HASH
            )
            vault_ph = vault_puzzle.get_tree_hash()
        vault_coin = Coin(settlement.coin.name(), vault_ph, ASSET_AMOUNT)
        # Spend the just-created vault in the same list; policy only needs the create.
        vault_spend = _coin_spend(
            vault_coin, vault_puzzle, Program.to([])
        )
        other.append(vault_spend)

    return bridging, locker_spend, other


def _wrap_views(locker_ph: bytes32):
    try:
        code = load_wrapped_cat_artifact_bytecode()
    except FileNotFoundError:
        return None
    return dict(
        wrapped_cat_runtime_code=code,
        view_portal=left_pad_32(ETH_PORTAL),
        view_other_chain=b"xch",
        view_locker_puzzle_hash=locker_ph,
    )


# ---------------------------------------------------------------------------
# 1. Chia ERC-20 unwrap (burn)
# ---------------------------------------------------------------------------


class TestChiaErc20Unwrap:
    def test_honest_full_melt_accepts(self):
        bridging, burner, cat = _build_erc20_burn()
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "accept"
        assert result.reason == "chia erc20 unwrap"

    def test_q_tail_rejects(self):
        bridging, burner, cat = _build_erc20_burn(tail=Q_TAIL)
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"
        assert "tail" in result.reason.lower() or "cat" in result.reason.lower()

    def test_tail_token_a_memo_token_b_rejects(self):
        bridging, burner, cat = _build_erc20_burn(
            asset=ASSET_A, memo_asset=ASSET_B
        )
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"

    def test_memo_destination_chain_swapped_rejects(self):
        bridging, burner, cat = _build_erc20_burn()
        real = spend_policy._extract_bridging_memo(
            Program.from_bytes(bytes(burner.puzzle_reveal)),
            Program.from_bytes(bytes(burner.solution)),
            burner.coin.amount,
        )
        assert real is not None

        def _swapped(*_a, **_k):
            _chain, dest, contents = real
            return b"bse", dest, contents

        with patch.object(spend_policy, "_extract_bridging_memo", _swapped):
            result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"
        assert "memo destination" in result.reason

    def test_partial_melt_extra_delta_rejects(self):
        bridging, burner, cat = _build_erc20_burn()
        assert cat is not None
        sol_args = list(Program.from_bytes(bytes(cat.solution)).as_iter())
        sol_args[6] = Program.to(0)  # not -amount
        cat = CoinSpend(
            cat.coin,
            cat.puzzle_reveal,
            SerializedProgram.from_bytes(bytes(Program.to(sol_args))),
        )
        result = validate_chia_erc20_unwrap(bridging, burner, cat, _routes())
        assert result.kind == "reject"
        assert "extra_delta" in result.reason

    def test_missing_cat_spend_rejects(self):
        bridging, burner, _ = _build_erc20_burn(include_cat=False)
        result = validate_chia_erc20_unwrap(bridging, burner, None, _routes())
        assert result.kind == "reject"
        assert result.reason == "missing CAT spend"

    def test_parent_not_burner_rejects(self):
        _, _, cat = _build_erc20_burn()
        fake = CoinSpend(
            Coin(bytes32(b"\x02" * 32), Program.to(1).get_tree_hash(), BURNER_AMOUNT),
            SerializedProgram.from_bytes(bytes(Program.to(1))),
            SerializedProgram.from_bytes(bytes(Program.to([]))),
        )
        bridging = Coin(fake.coin.name(), BRIDGING_PUZZLE_HASH, fake.coin.amount)
        result = validate_chia_erc20_unwrap(bridging, fake, cat, _routes())
        assert result.kind == "reject"
        assert "CAT_BURNER_MOD" in result.reason or "parent" in result.reason


# ---------------------------------------------------------------------------
# 2. Chia CAT / XCH wrap (lock)
# ---------------------------------------------------------------------------


class TestChiaCatWrap:
    def test_honest_cat_lock_with_change_accepts(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        asset_id = bytes32(b"\xab" * 32)
        bridging, locker, other = _build_lock(asset_id=asset_id, with_change=True)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "accept"

    def test_honest_xch_lock_accepts(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(asset_id=None)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "accept"

    def test_missing_vault_settlement_rejects(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            # Structural path still rejects before / without bytecode when views missing → retry
            bridging, locker, other = _build_lock(
                asset_id=None, omit_settlement=True
            )
            result = validate_chia_cat_wrap(
                bridging,
                locker,
                other,
                _routes(),
                wrapped_cat_runtime_code=None,
                view_portal=None,
                view_other_chain=None,
                view_locker_puzzle_hash=None,
            )
            assert result.kind == "retry"
            return
        bridging, locker, other = _build_lock(asset_id=None, omit_settlement=True)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "reject"
        assert "settlement" in result.reason

    def test_announcement_without_vault_create_rejects(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(
            asset_id=None, empty_settlement_payments=True
        )
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "reject"
        assert "settlement" in result.reason

    def test_vault_spent_in_same_list_still_accepts(self):
        views = _wrap_views(bytes32(b"\x00" * 32))
        if views is None:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(asset_id=None, spend_vault=True)
        views["view_locker_puzzle_hash"] = locker.coin.puzzle_hash
        result = validate_chia_cat_wrap(
            bridging, locker, other, _routes(), **views
        )
        assert result.kind == "accept"

    def test_parent_not_locker_rejects(self):
        fake = CoinSpend(
            Coin(bytes32(b"\x10" * 32), Program.to(1).get_tree_hash(), BRIDGING_TOLL),
            SerializedProgram.from_bytes(bytes(Program.to(1))),
            SerializedProgram.from_bytes(bytes(Program.to([]))),
        )
        bridging = Coin(fake.coin.name(), BRIDGING_PUZZLE_HASH, BRIDGING_TOLL)
        result = validate_chia_cat_wrap(
            bridging,
            fake,
            [],
            _routes(),
            wrapped_cat_runtime_code=b"\x00",
            view_portal=left_pad_32(ETH_PORTAL),
            view_other_chain=b"xch",
            view_locker_puzzle_hash=fake.coin.puzzle_hash,
        )
        assert result.kind == "reject"
        assert "LOCKER_MOD" in result.reason or "parent" in result.reason

    def test_bytecode_mismatch_rejects_when_artifacts_exist(self):
        try:
            code = load_wrapped_cat_artifact_bytecode()
        except FileNotFoundError:
            pytest.skip("WrappedCAT artifacts missing")
        bridging, locker, other = _build_lock(asset_id=None)
        result = validate_chia_cat_wrap(
            bridging,
            locker,
            other,
            _routes(),
            wrapped_cat_runtime_code=code + b"\xff",
            view_portal=left_pad_32(ETH_PORTAL),
            view_other_chain=b"xch",
            view_locker_puzzle_hash=locker.coin.puzzle_hash,
        )
        assert result.kind == "reject"
        assert "bytecode" in result.reason


# ---------------------------------------------------------------------------
# 3. EVM ERC-20 wrap tip / balance math
# ---------------------------------------------------------------------------


class TestEvmErc20Wrap:
    def _dest(self) -> bytes32:
        return get_cat_minter_puzzle(PORTAL, b"eth", ETH_BRIDGE).get_tree_hash()

    def _base_kwargs(self, gross: int = 10_000, tip_bps: int = 30, decimals: int = 18):
        tip = compute_bridge_tip(gross, tip_bps)
        net = gross - tip
        factor = 10 ** (decimals - 3)
        asset = ASSET_A
        return dict(
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(ETH_BRIDGE),
            destination=self._dest(),
            destination_chain=b"xch",
            contents=[asset, left_pad_32(RECEIVER), net.to_bytes(32, "big")],
            tx_status=1,
            tip_bps=tip_bps,
            decimals=decimals,
            gross_mojo_amount=gross,
            bridge_token_balance_diff=net * factor,
            balance_diff_asset=asset,
        )

    def test_tip_math_and_matching_diff_accepts(self):
        # tip = amount * bps / 10000; if 0 then tip is 1; message amount == net
        assert compute_bridge_tip(10_000, 30) == 30
        assert compute_bridge_tip(1, 30) == 1
        result = validate_evm_erc20_wrap(**self._base_kwargs())
        assert result.kind == "accept"

    def test_decimals_below_3_rejects(self):
        kwargs = self._base_kwargs(decimals=2)
        # recompute factor for contents net still ok; validator rejects decimals
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "reject"
        assert "decimals" in result.reason

    def test_tip_bps_out_of_range_rejects(self):
        for bad in (0, 1001):
            kwargs = self._base_kwargs(tip_bps=30)
            kwargs["tip_bps"] = bad
            result = validate_evm_erc20_wrap(**kwargs)
            assert result.kind == "reject"
            assert "tip_bps" in result.reason

    def test_balance_diff_none_retries(self):
        kwargs = self._base_kwargs()
        kwargs["bridge_token_balance_diff"] = None
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "retry"

    def test_balance_diff_asset_mismatch_rejects(self):
        kwargs = self._base_kwargs()
        kwargs["balance_diff_asset"] = ASSET_B
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "reject"
        assert "balance diff asset" in result.reason

    def test_message_amount_must_equal_net(self):
        kwargs = self._base_kwargs(gross=10_000, tip_bps=30)
        wrong_net = 10_000  # not net after tip
        kwargs["contents"] = [
            ASSET_A,
            left_pad_32(RECEIVER),
            wrong_net.to_bytes(32, "big"),
        ]
        result = validate_evm_erc20_wrap(**kwargs)
        assert result.kind == "reject"
        assert "message amount" in result.reason

    def test_ether_amount_after_toll_divisible(self):
        gross = 1000
        tip_bps = 30
        tip = compute_bridge_tip(gross, tip_bps)
        net = gross - tip
        decimals = 18
        factor = 10 ** (decimals - 3)
        ratio = 10**12
        toll = 10**15
        amount_after_toll = gross * ratio * factor
        msg_value = amount_after_toll + toll
        contents = [
            left_pad_32(MILLIETH),
            left_pad_32(RECEIVER),
            net.to_bytes(32, "big"),
        ]
        ok = validate_evm_erc20_wrap(
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(ETH_BRIDGE),
            destination=self._dest(),
            destination_chain=b"xch",
            contents=contents,
            tx_status=1,
            tip_bps=tip_bps,
            decimals=decimals,
            gross_mojo_amount=gross,
            bridge_token_balance_diff=net * factor,
            balance_diff_asset=left_pad_32(MILLIETH),
            is_bridge_ether=True,
            msg_value=msg_value,
            message_toll=toll,
            weth_to_eth_ratio=ratio,
        )
        assert ok.kind == "accept"

        bad = validate_evm_erc20_wrap(
            routes=_routes(),
            source_chain=b"eth",
            source=left_pad_32(ETH_BRIDGE),
            destination=self._dest(),
            destination_chain=b"xch",
            contents=contents,
            tx_status=1,
            tip_bps=tip_bps,
            decimals=decimals,
            gross_mojo_amount=gross,
            bridge_token_balance_diff=net * factor,
            balance_diff_asset=left_pad_32(MILLIETH),
            is_bridge_ether=True,
            msg_value=msg_value + 1,
            message_toll=toll,
            weth_to_eth_ratio=ratio,
        )
        assert bad.kind == "reject"
        assert "wethToEthRatio" in bad.reason


# ---------------------------------------------------------------------------
# 4. evm_address_from_bytes32
# ---------------------------------------------------------------------------


class TestEvmAddressFromBytes32:
    def test_left_padded_source_normalizes_to_20_bytes(self):
        source = bytes.fromhex("000bA7E6824AA002033Df7BEDf8bAB72Fc6465e9")
        assert len(source) == 20
        padded = left_pad_32(source)
        assert len(padded) == 32
        out = evm_address_from_bytes32(padded)
        assert len(out) == 20
        assert out == bytes.fromhex("000ba7e6824aa002033df7bedf8bab72fc6465e9")
        assert out != out[-19:]  # not truncated to 19 bytes

    def test_nonzero_prefix_rejects(self):
        addr20 = bytes.fromhex("000bA7E6824AA002033Df7BEDf8bAB72Fc6465e9")
        bad = b"\x01" + b"\x00" * 11 + addr20
        assert len(bad) == 32
        with pytest.raises(ValueError, match="non-zero prefix"):
            evm_address_from_bytes32(bad)
