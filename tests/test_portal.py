from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from blspy import AugSchemeMPL, PrivateKey, G1Element
from chia.util.bech32m import encode_puzzle_hash
from chia.wallet.puzzles.singleton_top_layer_v1_1 import generate_launcher_coin
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    launch_conditions_and_coinsol
from chia.types.coin_spend import CoinSpend
import pytest
import pytest_asyncio
import os
import sys
import random
import json

from tests.utils import *
from drivers.portal import *

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

@pytest_asyncio.fixture(scope="function")
async def validator_set():
    validator_treshold = 7
    validator_sks: List[PrivateKey] = []
    validator_pks: List[G1Element] = []
    for i in range(11):
        sk = AugSchemeMPL.key_gen(random.randbytes(32))
        validator_sks.append(sk)
        validator_pks.append(sk.get_g1())
    return validator_treshold, validator_sks, validator_pks

class TestPortal:
    @pytest.mark.asyncio
    async def test_healthz(self, setup):
        full_node_client: FullNodeRpcClient
        wallet_clients: List[WalletRpcClient]
        full_node_client, wallet_clients = setup

        full_node_resp = await full_node_client.healthz()
        assert full_node_resp['success']

        wallet_client: WalletRpcClient = wallet_clients[0]
        wallet_resp = await wallet_client.healthz()
        assert wallet_resp['success']


    @pytest.mark.asyncio
    async def test_receive_message_ph(self, setup, validator_set):
        node: FullNodeRpcClient
        wallets: List[WalletRpcClient]
        node, wallets = setup
        wallet = wallets[0]

        signature_treshold, validator_sks, validator_pks = validator_set

        # 1. Launch portal receiver
        one_puzzle = Program.to(1)
        one_puzzle_hash: bytes32 = Program(one_puzzle).get_tree_hash()
        one_address = encode_puzzle_hash(one_puzzle_hash, "txch")

        tx_record = await wallet.send_transaction(1, 1, one_address, get_tx_config(1))
        portal_launcher_parent: Coin = tx_record.additions[0]
        await wait_for_coin(node, portal_launcher_parent)

        portal_launcher = generate_launcher_coin(portal_launcher_parent, 1)
        portal_launcher_id = portal_launcher.name()

        portal_inner_puzzle = get_portal_receiver_inner_puzzle(
            portal_launcher_id,
            signature_treshold,
            validator_pks,
        )
        portal_full_puzzle = puzzle_for_singleton(
            portal_launcher_id,
            portal_inner_puzzle,
        )
        portal_full_puzzle_hash = portal_full_puzzle.get_tree_hash()
        portal = Coin(portal_launcher_id, portal_full_puzzle_hash, 1)

        conditions, portal_launcher_spend = launch_conditions_and_coinsol(
            portal_launcher_parent,
            portal_inner_puzzle,
            [],
            1
        )
        portal_launcher_parent_spend = CoinSpend(portal_launcher_parent, one_puzzle, Program.to(conditions))

        portal_creation_bundle = SpendBundle(
            [portal_launcher_parent_spend, portal_launcher_spend],
            AugSchemeMPL.aggregate([])
        )
        open("/tmp/sb.json", "w").write(json.dumps(portal_creation_bundle.to_json_dict(), indent=4))
        await node.push_tx(portal_creation_bundle)
        await wait_for_coin(node, portal)

        print('Done :)')
