from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from blspy import AugSchemeMPL
from chia.util.bech32m import encode_puzzle_hash
from chia.wallet.puzzles.singleton_top_layer_v1_1 import generate_launcher_coin
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    launch_conditions_and_coinsol, solution_for_singleton, lineage_proof_for_coinsol
from chia.wallet.puzzles.singleton_top_layer_v1_1 import puzzle_for_singleton
from chia.types.coin_spend import CoinSpend
from chia.types.condition_opcodes import ConditionOpcode
from chia.wallet.cat_wallet.cat_utils import construct_cat_puzzle
from chia.wallet.cat_wallet.cat_utils import CAT_MOD
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.wallet.cat_wallet.cat_utils import SpendableCAT
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.cat_wallet.cat_utils import \
    unsigned_spend_bundle_for_spendable_cats
import pytest
import time
import json

from tests.utils import *
from drivers.wrapped_assets import *
from drivers.portal import get_message_coin_puzzle, get_message_coin_solution

NONCE = 1337
SENDER = to_eth_address("eth_token_master")
DEADLINE = int(time.time()) + 24 * 60 * 60
BRIDGING_PUZZLE_HASH = encode_bytes32("bridge")
ERC20_ASSET_CONTRACT = to_eth_address("erc20")
ETH_RECEIVER = to_eth_address("eth_receiver")

class TestPortal:
    @pytest.mark.asyncio
    async def test_wrapped_asset_mint_and_burn(self, setup):
        node: FullNodeRpcClient
        wallets: List[WalletRpcClient]
        node, wallets = setup
        wallet = wallets[0]

        # 1. Launch mock portal receiver (inner_puzzle = one_puzzle)
        one_puzzle = Program.to(1)
        one_puzzle_hash: bytes32 = Program(one_puzzle).get_tree_hash()
        one_address = encode_puzzle_hash(one_puzzle_hash, "txch")

        tx_record = await wallet.send_transaction(1, 1, one_address, get_tx_config(1))
        portal_launcher_parent: Coin = tx_record.additions[0]
        await wait_for_coin(node, portal_launcher_parent)

        portal_launcher = generate_launcher_coin(portal_launcher_parent, 1)
        portal_launcher_id = portal_launcher.name()

        portal_full_puzzle = puzzle_for_singleton(
            portal_launcher_id,
            one_puzzle,
        )
        portal_full_puzzle_hash = portal_full_puzzle.get_tree_hash()
        portal = Coin(portal_launcher_id, portal_full_puzzle_hash, 1)

        conditions, portal_launcher_spend = launch_conditions_and_coinsol(
            portal_launcher_parent,
            one_puzzle,
            [],
            1
        )
        portal_launcher_parent_spend = CoinSpend(portal_launcher_parent, one_puzzle, Program.to(conditions))

        portal_creation_bundle = SpendBundle(
            [portal_launcher_parent_spend, portal_launcher_spend],
            AugSchemeMPL.aggregate([])
        )
        await node.push_tx(portal_creation_bundle)
        await wait_for_coin(node, portal)

        # 2. Create message coin
        minter_puzzle = get_cat_minter_puzzle(portal_launcher_id, BRIDGING_PUZZLE_HASH, SENDER)
        minter_puzzle_hash = minter_puzzle.get_tree_hash()

        receiver_puzzle: Program = one_puzzle
        receiver_puzzle_hash = one_puzzle_hash

        message: Program = Program.to([
            ERC20_ASSET_CONTRACT,
            receiver_puzzle_hash,
            10000 # 10.000 CATs
        ])

        message_coin_puzzle = get_message_coin_puzzle(
            portal_launcher_id,
            SENDER,
            minter_puzzle_hash,
            True,
            DEADLINE,
            message.get_tree_hash()
        )
        message_coin_puzzle_hash = message_coin_puzzle.get_tree_hash()

        portal_inner_solution = Program.to([
            [ConditionOpcode.CREATE_COIN, one_puzzle_hash, 1], # recreate
            [ConditionOpcode.CREATE_COIN, message_coin_puzzle_hash, 0], # create message
        ])
        portal_solution = solution_for_singleton(
            lineage_proof_for_coinsol(portal_launcher_spend),
            1,
            portal_inner_solution
        )


        message_coin_creation_spend = CoinSpend(
            portal,
            portal_full_puzzle,
            portal_solution
        )
        message_coin_creation_bundle = SpendBundle(
            [message_coin_creation_spend],
            AugSchemeMPL.aggregate([])
        )
        
        await node.push_tx(message_coin_creation_bundle)

        message_coin = Coin(
            portal.name(),
            message_coin_puzzle_hash,
            0
        )
        await wait_for_coin(node, message_coin)

        # 3. Claim message coin & mint asset
        minter_address = encode_puzzle_hash(minter_puzzle_hash, "txch")
        tx_record = await wallet.send_transaction(1, 10000, minter_address, get_tx_config(10000))
        minter_coin: Coin = tx_record.additions[0]
        await wait_for_coin(node, minter_coin)

        message_coin_solution = get_message_coin_solution(
            minter_coin,
            portal.parent_coin_info,
            one_puzzle_hash,
            message_coin.name()
        )
        message_coin_spend = CoinSpend(
            message_coin,
            message_coin_puzzle,
            message_coin_solution
        )

        minter_puzzle_solution = get_cat_minter_puzzle_solution(
            DEADLINE,
            message,
            minter_puzzle_hash,
            minter_coin.name(),
            message_coin.parent_coin_info
        )
        minter_coin_spend = CoinSpend(
            minter_coin,
            minter_puzzle,
            minter_puzzle_solution
        )

        mint_bundle = SpendBundle(
            [message_coin_spend, minter_coin_spend],
            AugSchemeMPL.aggregate([])
        )

        await node.push_tx(mint_bundle)

        # 4. Spend freshly-minted CAT coin
        wrapped_asset_tail = get_wrapped_tail(
            portal_launcher_id,
            BRIDGING_PUZZLE_HASH,
            SENDER,
            ERC20_ASSET_CONTRACT
        )
        wrapped_asset_tail_hash = wrapped_asset_tail.get_tree_hash()

        cat_mint_and_payout_puzzle = get_cat_mint_and_payout_inner_puzzle(receiver_puzzle_hash)

        cat_coin_puzzle = construct_cat_puzzle(
            CAT_MOD,
            wrapped_asset_tail_hash,
            cat_mint_and_payout_puzzle,
            CAT_MOD_HASH
        )
        cat_coin_puzzle_hash = cat_coin_puzzle.get_tree_hash()

        cat_coin = Coin(
            minter_coin.name(),
            cat_coin_puzzle_hash,
            10000
        )
       
        cat_inner_solution = get_cat_mint_and_payout_inner_puzzle_solution(
            wrapped_asset_tail,
            cat_coin.amount,
            minter_coin.parent_coin_info
        )
        cat = SpendableCAT(
            cat_coin,
            wrapped_asset_tail_hash,
            cat_mint_and_payout_puzzle,
            cat_inner_solution,
            limitations_solution=minter_coin.parent_coin_info,
            limitations_program_reveal=wrapped_asset_tail
        )

        cat_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, [cat]
        )
        await node.push_tx(cat_spend_bundle)

        one_cat_puzzle = construct_cat_puzzle(
            CAT_MOD,
            wrapped_asset_tail_hash,
            one_puzzle,
            CAT_MOD_HASH
        )
        one_cat_puzzle_hash = one_cat_puzzle.get_tree_hash()
        one_cat_coin = Coin(
            cat_coin.name(),
            one_cat_puzzle_hash,
            10000
        )
        await wait_for_coin(node, one_cat_coin)

        # 5. Burn CAT coin
        burner_puzzle = get_cat_burner_puzzle(
            BRIDGING_PUZZLE_HASH,
            SENDER,
        )
        burner_puzzle_hash = burner_puzzle.get_tree_hash()

        burner_address = encode_puzzle_hash(burner_puzzle_hash, "txch")
        tx_record = await wallet.send_transaction(1, 1, burner_address, get_tx_config(1))
        burner_coin: Coin = tx_record.additions[0]
        await wait_for_coin(node, burner_coin)

        cat_burn_inner_puzzle = get_cat_brun_inner_puzzle(
            BRIDGING_PUZZLE_HASH,
            SENDER,
            ERC20_ASSET_CONTRACT,
            ETH_RECEIVER
        )
        cat_burn_inner_puzzle_hash = cat_burn_inner_puzzle.get_tree_hash()

        one_cat_inner_solution = Program.to([
            [ConditionOpcode.CREATE_COIN, cat_burn_inner_puzzle_hash, 10000],
        ])
        one_cat = SpendableCAT(
            one_cat_coin,
            wrapped_asset_tail_hash,
            one_puzzle,
            one_cat_inner_solution,
            lineage_proof=LineageProof(
                cat_coin.parent_coin_info,
                cat_mint_and_payout_puzzle.get_tree_hash(),
                cat_coin.amount
            )
        )

        one_cat_spend = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, [one_cat]
        ).coin_spends[0]

        last_cat_full_puzzle = construct_cat_puzzle(
            CAT_MOD,
            wrapped_asset_tail_hash,
            cat_burn_inner_puzzle,
            CAT_MOD_HASH
        )
        last_cat_coin = Coin(
            one_cat_coin.name(),
            last_cat_full_puzzle.get_tree_hash(),
            10000
        )

        cat_burn_inner_solution = get_burn_inner_puzzle_solution(
            burner_coin.parent_coin_info,
            1,
            last_cat_coin.name(),
            wrapped_asset_tail
        )
        last_cat = SpendableCAT(
            last_cat_coin,
            wrapped_asset_tail_hash,
            cat_burn_inner_puzzle,
            cat_burn_inner_solution,
            lineage_proof=LineageProof(
                one_cat_coin.parent_coin_info,
                one_puzzle_hash,
                one_cat_coin.amount
            ),
            extra_delta=-last_cat_coin.amount,
            limitations_program_reveal=wrapped_asset_tail,
            limitations_solution=raw_hash([b'\x01', ETH_RECEIVER])
        )
        last_cat_spend = unsigned_spend_bundle_for_spendable_cats(
            CAT_MOD, [last_cat]
        ).coin_spends[0]
        
        burner_solution = get_cat_burner_puzzle_solution(
            last_cat_coin.parent_coin_info,
            wrapped_asset_tail_hash,
            10000,
            ERC20_ASSET_CONTRACT,
            ETH_RECEIVER,
            int(time.time()) - 24 * 60 * 60,
            burner_coin
        )
        burner_spend = CoinSpend(
            burner_coin,
            burner_puzzle,
            burner_solution
        )

        burn_spend_bundle = SpendBundle(
            [one_cat_spend, last_cat_spend, burner_spend],
            AugSchemeMPL.aggregate([])
        )
        await node.push_tx(burn_spend_bundle)
