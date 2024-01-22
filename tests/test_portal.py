from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from blspy import AugSchemeMPL, PrivateKey, G1Element, G2Element
from chia.util.bech32m import encode_puzzle_hash
from chia.wallet.puzzles.singleton_top_layer_v1_1 import generate_launcher_coin
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    launch_conditions_and_coinsol, solution_for_singleton, lineage_proof_for_coinsol
from chia.types.coin_spend import CoinSpend
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.types.condition_opcodes import ConditionOpcode
from chia.util.bech32m import decode_puzzle_hash
import pytest
import pytest_asyncio
import random
import time
import json

from tests.utils import *
from drivers.portal import *

VALIDATOR_TRESHOLD = 7
VALIDATOR_SIG_SWITCHES = [1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0]
NONCE = 1337
SENDER = to_eth_address("sender")
DEADLINE = int(time.time()) + 24 * 60 * 60
MESSAGE = Program.to(["yaku", "hito", 1337])

assert len(VALIDATOR_SIG_SWITCHES) == 11
assert sum(VALIDATOR_SIG_SWITCHES) == VALIDATOR_TRESHOLD

@pytest_asyncio.fixture(scope="function")
async def validator_set():
    validator_sks: List[PrivateKey] = []
    validator_pks: List[G1Element] = []
    for i in range(11):
        sk = AugSchemeMPL.key_gen(random.randbytes(32))
        validator_sks.append(sk)
        validator_pks.append(sk.get_g1())
    return validator_sks, validator_pks

def get_validator_set_sigs(
    message: bytes,
    validator_sks: List[PrivateKey],
    validator_sig_switches: List[bool]
) -> List[G2Element]:
    sigs = []
    for i, use_sig in enumerate(validator_sig_switches):
        if not use_sig:
            continue

        sig = AugSchemeMPL.sign(validator_sks[i], message)
        sigs.append(sig)
    
    return sigs

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

    async def do_ckeck(self, setup, validator_set, with_ph):
        node: FullNodeRpcClient
        wallets: List[WalletRpcClient]
        node, wallets = setup
        wallet = wallets[0]

        validator_sks, validator_pks = validator_set

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
            VALIDATOR_TRESHOLD,
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
        await node.push_tx(portal_creation_bundle)
        await wait_for_coin(node, portal)

        # 1.5 Launch claimer coin
        tx_record = await wallet.send_transaction(1, 1, one_address, get_tx_config(1))
        message_claimer: Coin = tx_record.additions[0]
        await wait_for_coin(node, message_claimer)
        
        message_claimer_launcher_id: bytes32
        message_claimer_creation_bundle: SpendBundle
        message_claimer_full_puzzle: Program = one_puzzle
        if not with_ph:
            message_claimer_launcher_parent = message_claimer
            message_claimer_launcher = generate_launcher_coin(message_claimer_launcher_parent, 1)
            message_claimer_launcher_id = message_claimer_launcher.name()

            message_claimer_inner_puzzle = one_puzzle
            message_claimer_full_puzzle = puzzle_for_singleton(
                message_claimer_launcher_id,
                message_claimer_inner_puzzle,
            )
            message_claimer_full_puzzle_hash = message_claimer_full_puzzle.get_tree_hash()
            message_claimer = Coin(message_claimer_launcher_id, message_claimer_full_puzzle_hash, 1)

            conditions, message_claimer_launcher_spend = launch_conditions_and_coinsol(
                message_claimer_launcher_parent,
                message_claimer_inner_puzzle,
                [],
                1
            )
            message_claimer_launcher_parent_spend = CoinSpend(
                message_claimer_launcher_parent,
                one_puzzle,
                Program.to(conditions)
            )

            message_claimer_creation_bundle = SpendBundle(
                [message_claimer_launcher_parent_spend, message_claimer_launcher_spend],
                AugSchemeMPL.aggregate([])
            )
            await node.push_tx(message_claimer_creation_bundle)
            await wait_for_coin(node, message_claimer)

        # 2. Send message via portal (to the '1' puzzle)
        new_portal_inner_puzzle = get_portal_receiver_inner_puzzle(
            portal_launcher_id,
            VALIDATOR_TRESHOLD,
            validator_pks,
            last_nonces=[NONCE]
        )
        new_portal_inner_puzzle_hash: bytes32 = Program(new_portal_inner_puzzle).get_tree_hash()
        
        target = one_puzzle_hash if with_ph else message_claimer_launcher_id
        msg = PortalMessage(
            nonce=NONCE,
            validator_sig_switches=VALIDATOR_SIG_SWITCHES,
            sender=SENDER,
            target=target,
            target_is_puzzle_hash=with_ph,
            deadline=DEADLINE,
            message=MESSAGE
        )
        portal_inner_solution = get_portal_receiver_inner_solution(
            [msg]
        )
        portal_solution = solution_for_singleton(
            lineage_proof_for_coinsol(portal_launcher_spend),
            1,
            portal_inner_solution
        )

        message_to_sign: bytes = Program(Program.to([
            0, # no update to inner puzzle hash
            NONCE,
            SENDER,
            target,
            1 if with_ph else 0,
            DEADLINE,
            MESSAGE
        ])).get_tree_hash()
        message_to_sign += portal.name() + DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA
        message_signature = AugSchemeMPL.aggregate(
            get_validator_set_sigs(
                message_to_sign,
                validator_sks,
                VALIDATOR_SIG_SWITCHES
            )
        )

        portal_spend_bundle = SpendBundle(
            [CoinSpend(portal, portal_full_puzzle, portal_solution)],
            message_signature
        )

        await node.push_tx(portal_spend_bundle)
        await wait_for_coin(node, portal, also_wait_for_spent=True)

        message_coin_puzzle = get_message_coin_puzzle(
            portal_launcher_id,
            SENDER,
            target,
            with_ph,
            DEADLINE,
            Program(MESSAGE).get_tree_hash()
        )
        message_coin = Coin(
            portal.name(),
            message_coin_puzzle.get_tree_hash(),
            0
        )

        await wait_for_coin(node, message_coin)

        # 3. Receive message via message coin

        message_coin_solution: Program
        if with_ph:
            message_coin_solution = get_message_coin_solution(
                message_claimer,
                portal.parent_coin_info,
                portal_inner_puzzle.get_tree_hash(),
                message_coin.name()
            )
        else:
            message_coin_solution = get_message_coin_solution(
                message_claimer,
                portal.parent_coin_info,
                portal_inner_puzzle.get_tree_hash(),
                message_coin.name(),
                receiver_singleton_launcher_id=message_claimer_launcher_id,
                receiver_singleton_inner_puzzle_hash=one_puzzle_hash
            )
        message_coin_spend = CoinSpend(
            message_coin,
            message_coin_puzzle,
            message_coin_solution
        )

        my_puzzle_hash = decode_puzzle_hash(await wallet.get_next_address(1, False))
        message_claimer_solution = Program.to([
            [ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, message_coin.name()],
            [ConditionOpcode.CREATE_COIN, my_puzzle_hash, 1]
        ])
        if not with_ph:
            message_claimer_inner_solution = message_claimer_solution
            message_claimer_solution = solution_for_singleton(
                lineage_proof_for_coinsol(message_claimer_launcher_spend),
                1,
                message_claimer_inner_solution
            )
        
        message_claimer_spend = CoinSpend(
            message_claimer,
            message_claimer_full_puzzle,
            message_claimer_solution
        )

        message_claim_bundle = SpendBundle(
            [message_coin_spend, message_claimer_spend],
            AugSchemeMPL.aggregate([])
        )

        await node.push_tx(message_claim_bundle)
        await wait_for_coin(node, message_coin, also_wait_for_spent=True)

    @pytest.mark.asyncio
    async def test_receive_message_ph(self, setup, validator_set):
        await self.do_ckeck(setup, validator_set, True)

    @pytest.mark.asyncio
    async def test_receive_message_singleton(self, setup, validator_set):
        await self.do_ckeck(setup, validator_set, False)
