from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia_rs import AugSchemeMPL, PrivateKey, G1Element, G2Element
from chia.util.bech32m import encode_puzzle_hash
from chia.wallet.puzzles.singleton_top_layer_v1_1 import generate_launcher_coin
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    launch_conditions_and_coinsol, solution_for_singleton, lineage_proof_for_coinsol
from chia.types.coin_spend import CoinSpend
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.types.condition_opcodes import ConditionOpcode
from chia.util.bech32m import decode_puzzle_hash
from chia.util.keychain import bytes_to_mnemonic, mnemonic_to_seed
from commands.keys import mnemonic_to_validator_pk
import secrets
import pytest
import pytest_asyncio
import random
import time
import json

from tests.utils import *
from drivers.portal import *
from drivers.multisig import *

VALIDATOR_THRESHOLD = 7
VALIDATOR_SIG_SWITCHES = [1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0]
NEW_VALIDATOR_THRESHOLD = 6
NEW_VALIDATOR_SIG_SWITCHES = [1, 0, 0, 1, 1, 0, 1, 1, 0, 1, 0]
NONCE = 1337
SOURCE_CHAIN = 'eth'
SOURCE = to_eth_address("sender")
MESSAGE = Program.to(["yaku", "hito", 1337])

assert len(VALIDATOR_SIG_SWITCHES) == 11
assert sum(VALIDATOR_SIG_SWITCHES) == VALIDATOR_THRESHOLD
assert len(NEW_VALIDATOR_SIG_SWITCHES) == 11
assert sum(NEW_VALIDATOR_SIG_SWITCHES) == NEW_VALIDATOR_THRESHOLD

async def get_validator_set():
    validator_sks: List[PrivateKey] = []
    validator_pks: List[G1Element] = []
    for i in range(11):
        sk = AugSchemeMPL.key_gen(random.randbytes(32))
        validator_sks.append(sk)
        validator_pks.append(sk.get_g1())
    return validator_sks, validator_pks

@pytest_asyncio.fixture(scope="function")
async def validator_set():
    return await get_validator_set()

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

    @pytest.mark.asyncio
    async def test_receive_message_and_upgrade(self, setup, validator_set):
        node: FullNodeRpcClient
        wallets: List[WalletRpcClient]
        node, wallets = setup
        wallet = wallets[0]

        validator_sks, validator_pks = validator_set

        # 1. Launch portal receiver
        one_puzzle = Program.to(1)
        one_puzzle_hash: bytes32 = Program(one_puzzle).get_tree_hash()
        one_address = encode_puzzle_hash(one_puzzle_hash, "txch")

        portal_updater_puzzle = get_multisig_inner_puzzle(
            validator_pks,
            VALIDATOR_THRESHOLD,
        )
        portal_updater_puzzle_hash = portal_updater_puzzle.get_tree_hash()

        tx_record = await wallet.send_transaction(1, 1, one_address, get_tx_config(1))
        portal_launcher_parent: Coin = tx_record.additions[0]
        await wait_for_coin(node, portal_launcher_parent)

        portal_launcher = generate_launcher_coin(portal_launcher_parent, 1)
        portal_launcher_id = portal_launcher.name()

        portal_inner_puzzle = get_portal_receiver_inner_puzzle(
            portal_launcher_id,
            VALIDATOR_THRESHOLD,
            validator_pks,
            portal_updater_puzzle_hash
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
        
        # 2. Send message via portal (to the '1' puzzle)
        new_portal_inner_puzzle = get_portal_receiver_inner_puzzle(
            portal_launcher_id,
            VALIDATOR_THRESHOLD,
            validator_pks,
            portal_updater_puzzle_hash,
            last_chains_and_nonces=[(SOURCE_CHAIN, NONCE)]
        )
        new_portal_puzzle_hash: bytes32 = puzzle_for_singleton(
            portal_launcher_id,
            new_portal_inner_puzzle
        ).get_tree_hash()

        target = one_puzzle_hash
        msg = PortalMessage(
            nonce=NONCE,
            validator_sig_switches=VALIDATOR_SIG_SWITCHES,
            source_chain=SOURCE_CHAIN,
            source=SOURCE,
            destination=target,
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

        # nonce source_chain source destination message
        message_to_sign: bytes = Program(Program.to([
            SOURCE_CHAIN,
            NONCE,
            SOURCE,
            target,
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

        new_portal = Coin(portal.name(), new_portal_puzzle_hash, 1)
        await wait_for_coin(node, new_portal)

        message_coin_puzzle = get_message_coin_puzzle(
            portal_launcher_id,
            SOURCE_CHAIN,
            SOURCE,
            NONCE,
            target,
            Program(MESSAGE).get_tree_hash(),
        )
        message_coin = Coin(
            portal.name(),
            message_coin_puzzle.get_tree_hash(),
            0
        )

        await wait_for_coin(node, message_coin)

        # 3. Receive message via message coin

        message_coin_solution = get_message_coin_solution(
            message_claimer,
            portal.parent_coin_info,
            portal_inner_puzzle.get_tree_hash(),
            message_coin.name()
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
        
        message_claimer_spend = CoinSpend(
            message_claimer,
            one_puzzle,
            message_claimer_solution
        )

        message_claim_bundle = SpendBundle(
            [message_coin_spend, message_claimer_spend],
            AugSchemeMPL.aggregate([])
        )

        await node.push_tx(message_claim_bundle)
        await wait_for_coin(node, message_coin, also_wait_for_spent=True)

        # 4. Update portal key set
        new_validator_sks, new_validator_pks = await get_validator_set()
        updater_delegated_puzzle = get_portal_rekey_delegated_puzzle(
            portal_launcher_id,
            VALIDATOR_THRESHOLD,
            validator_pks,
            NEW_VALIDATOR_THRESHOLD,
            new_validator_pks,
            VALIDATOR_THRESHOLD,
            validator_pks,
            NEW_VALIDATOR_THRESHOLD,
            new_validator_pks
        
        )
        updater_delegated_solution = get_portal_rekey_delegated_solution(
            [(SOURCE_CHAIN, NONCE)]
        )

        portal_updater_solution = get_multisig_inner_solution(
            VALIDATOR_THRESHOLD,
            VALIDATOR_SIG_SWITCHES,
            updater_delegated_puzzle,
            updater_delegated_solution
        )

        portal_inner_solution = get_portal_receiver_inner_solution(
            [],
            update_puzzle_reveal=portal_updater_puzzle,
            update_puzzle_solution=portal_updater_solution
        )
        portal_solution = solution_for_singleton(
            lineage_proof_for_coinsol(portal_spend_bundle.coin_spends[0]),
            1,
            portal_inner_solution
        )

        portal_puzzle = puzzle_for_singleton(
            portal_launcher_id,
            new_portal_inner_puzzle,
        )
        portal_update_spend = CoinSpend(new_portal, portal_puzzle, portal_solution)

        sigs = get_validator_set_sigs(
            updater_delegated_puzzle.get_tree_hash(),
            validator_sks,
            VALIDATOR_SIG_SWITCHES
        )
        portal_update_spend_bundle = SpendBundle([portal_update_spend], AugSchemeMPL.aggregate(sigs))

        await node.push_tx(portal_update_spend_bundle)
        await wait_for_coin(node, new_portal, also_wait_for_spent=True)

        # 5. Test that the new portal can receive messages
        portal_updater_puzzle = get_multisig_inner_puzzle(
            new_validator_pks,
            NEW_VALIDATOR_THRESHOLD,
        )
        portal_updater_puzzle_hash = portal_updater_puzzle.get_tree_hash()

        portal_inner_puzzle = get_portal_receiver_inner_puzzle(
            portal_launcher_id,
            NEW_VALIDATOR_THRESHOLD,
            new_validator_pks,
            portal_updater_puzzle_hash
        )
        portal_full_puzzle = puzzle_for_singleton(
            portal_launcher_id,
            portal_inner_puzzle,
        )
        portal_full_puzzle_hash = portal_full_puzzle.get_tree_hash()
        new_portal: Coin
        portal = Coin(new_portal.name(), portal_full_puzzle_hash, 1)
        # run -d '(mod () (x))'
        target = Program.from_bytes(bytes.fromhex("ff0880")).get_tree_hash()
        msg = PortalMessage(
            nonce=NONCE + 1,
            validator_sig_switches=NEW_VALIDATOR_SIG_SWITCHES,
            source_chain=SOURCE_CHAIN,
            source=SOURCE,
            destination=target,
            message=MESSAGE
        )
        portal_inner_solution = get_portal_receiver_inner_solution(
            [msg]
        )
        portal_solution = solution_for_singleton(
            lineage_proof_for_coinsol(portal_update_spend),
            1,
            portal_inner_solution
        )

        # nonce source_chain source destination message
        message_to_sign: bytes = Program(Program.to([
            SOURCE_CHAIN,
            NONCE + 1,
            SOURCE,
            target,
            MESSAGE
        ])).get_tree_hash()
        message_to_sign += portal.name() + DEFAULT_CONSTANTS.AGG_SIG_ME_ADDITIONAL_DATA
        message_signature = AugSchemeMPL.aggregate(
            get_validator_set_sigs(
                message_to_sign,
                new_validator_sks,
                NEW_VALIDATOR_SIG_SWITCHES
            )
        )

        portal_spend_bundle = SpendBundle(
            [
                CoinSpend(portal, portal_full_puzzle, portal_solution)
            ],
            message_signature
        )

        await node.push_tx(portal_spend_bundle)
        await wait_for_coin(node, portal, also_wait_for_spent=True)

        # 6. Update again, to verify initial updater puzzle update :)
        # update from new set -> old set
        updater_delegated_puzzle = get_portal_rekey_delegated_puzzle(
            portal_launcher_id,
            NEW_VALIDATOR_THRESHOLD,
            new_validator_pks,
            VALIDATOR_THRESHOLD,
            validator_pks,
            NEW_VALIDATOR_THRESHOLD,
            new_validator_pks,
            VALIDATOR_THRESHOLD,
            validator_pks
        
        )
        updater_delegated_solution = get_portal_rekey_delegated_solution(
            [(SOURCE_CHAIN, NONCE + 1)]
        )

        portal_updater_solution = get_multisig_inner_solution(
            NEW_VALIDATOR_THRESHOLD,
            NEW_VALIDATOR_SIG_SWITCHES,
            updater_delegated_puzzle,
            updater_delegated_solution
        )

        portal_inner_solution = get_portal_receiver_inner_solution(
            [],
            update_puzzle_reveal=portal_updater_puzzle,
            update_puzzle_solution=portal_updater_solution
        )
        portal_solution = solution_for_singleton(
            lineage_proof_for_coinsol(portal_spend_bundle.coin_spends[0]),
            1,
            portal_inner_solution
        )

        portal_inner_puzzle = get_portal_receiver_inner_puzzle(
            portal_launcher_id,
            NEW_VALIDATOR_THRESHOLD,
            new_validator_pks,
            portal_updater_puzzle_hash,
            last_chains_and_nonces=[(SOURCE_CHAIN, NONCE + 1)]
        )
        portal_puzzle = puzzle_for_singleton(
            portal_launcher_id,
            portal_inner_puzzle,
        )

        portal = Coin(portal.name(), portal_puzzle.get_tree_hash(), 1)
        portal_update_spend2 = CoinSpend(portal, portal_puzzle, portal_solution)

        sigs = get_validator_set_sigs(
            updater_delegated_puzzle.get_tree_hash(),
            new_validator_sks,
            NEW_VALIDATOR_SIG_SWITCHES
        )
        portal_update_spend_bundle2 = SpendBundle([portal_update_spend2], AugSchemeMPL.aggregate(sigs))

        await node.push_tx(portal_update_spend_bundle2)
        await wait_for_coin(node, new_portal, also_wait_for_spent=True)
