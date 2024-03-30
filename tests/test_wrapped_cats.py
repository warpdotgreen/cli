from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia_rs import AugSchemeMPL
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
from chia.util.condition_tools import conditions_dict_for_solution
from chia.types.blockchain_format.program import INFINITE_COST
from chia.wallet.cat_wallet.cat_utils import \
    unsigned_spend_bundle_for_spendable_cats
from chia.wallet.trading.offer import Offer, OFFER_MOD, OFFER_MOD_HASH
import pytest
import json

from tests.utils import *
from drivers.wrapped_cats import *
from drivers.portal import get_message_coin_puzzle, get_message_coin_solution

NONCE = 1337
SOURCE_CHAIN = b'eth'
SOURCE = to_eth_address("just_a_constract")
BRIDGING_PUZZLE_HASH = encode_bytes32("bridge")
SOURCE_CHAIN_TOKEN_CONTRACT_ADDRESS = to_eth_address("erc20")
ETH_RECEIVER = to_eth_address("eth_receiver")

BRIDGING_FEE = 10 ** 9

class TestWrappedCATs:
    @pytest.mark.asyncio
    async def test_wrapped_cats_lock_and_unlock(self, setup):
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

        # 2. Launch mock CATs
        resp = await wallet.create_new_cat_and_wallet(1337000, test=True)
        assert resp["success"]

        asset_id = bytes.fromhex(resp["asset_id"])
        cat_wallet_id = resp["wallet_id"]

        while (await wallet.get_wallet_balance(cat_wallet_id))["confirmed_wallet_balance"] == 0:
            time.sleep(0.1)

        # 3. Generate offer to lock CATs
        offer_dict = {}
        offer_dict[1] = -BRIDGING_FEE
        offer_dict[cat_wallet_id] = -1337000

        offer: Offer
        offer, _ = await wallet.create_offer_for_ids(offer_dict, get_tx_config(1), fee=100)

        # 4. Lock CATs
        offer_sb = offer.to_spend_bundle()
        coin_spends = list(offer_sb.coin_spends)

        xch_source_coin: Coin = None
        cat_source_coin: Coin = None
        cat_source_lineage_proof: Coin = None

        for coin_spend in coin_spends:
            coin: Coin = coin_spend.coin

            conditions: Program
            _, conditions = coin_spend.puzzle_reveal.run_with_cost(INFINITE_COST, coin_spend.solution)
            
            for condition in conditions.as_iter():
                cond = [_ for _ in condition.as_iter()]

                if cond[0] != b'\x33': # not CREATE_COIN
                    continue

                if cond[1] == OFFER_MOD_HASH:
                    xch_source_coin = Coin(
                        coin.name(),
                        OFFER_MOD_HASH,
                        cond[2].as_int()
                    )
                else: 
                    mod, args = coin_spend.puzzle_reveal.uncurry()
                    args = [_ for _ in args.as_iter()]
                    if mod != CAT_MOD or len(args) < 3:
                        continue

                    if bytes(args[1])[1:] != asset_id:
                        print(asset_id.hex(), bytes(args[1])[1:].hex())
                        continue
                    
                    cat_source_coin_puzzle = construct_cat_puzzle(
                        CAT_MOD,
                        asset_id,
                        OFFER_MOD,
                        CAT_MOD_HASH
                    )
                    cat_source_coin_puzzle_hash = cat_source_coin_puzzle.get_tree_hash()

                    if cond[1] != cat_source_coin_puzzle_hash:
                        print(":(((")
                        continue

                    cat_source_coin = Coin(
                        coin.name(),
                        cat_source_coin_puzzle_hash,
                        cond[2].as_int()
                    )
                    cat_source_lineage_proof = Coin(
                        coin.parent_coin_info,
                        args[2].get_tree_hash(),
                        coin.amount
                    )

        print(xch_source_coin)
        print(cat_source_coin)
        print(cat_source_lineage_proof)
        lock_puzzle = get_locker_puzzle(
            SOURCE_CHAIN,
            SOURCE,
            portal_launcher_id,
            BRIDGING_PUZZLE_HASH,
            asset_id
        )

        sb = SpendBundle(
            coin_spends, offer_sb.aggregated_signature
        )
        open("/tmp/sb.json", "w").write(json.dumps(sb.to_json_dict(), indent=4))
