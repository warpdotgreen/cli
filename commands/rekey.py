import click
from commands.cli_wrappers import *
from chia.wallet.puzzles.singleton_top_layer_v1_1 import pay_to_singleton_puzzle
from chia.wallet.puzzles.singleton_top_layer_v1_1 import claim_p2_singleton, pay_to_singleton_puzzle
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_record import CoinRecord
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.types.coin_spend import CoinSpend, compute_additions
from chia.util.condition_tools import conditions_dict_for_solution
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.wallet.trading.offer import OFFER_MOD
from commands.keys import mnemonic_to_validator_pk
from chia.util.bech32m import decode_puzzle_hash
from chia.wallet.trading.offer import OFFER_MOD_HASH
from typing import List
from chia_rs import PrivateKey, AugSchemeMPL, G1Element, G2Element
from chia.types.spend_bundle import SpendBundle
from chia.types.blockchain_format.program import INFINITE_COST
from typing import Tuple
from drivers.multisig import *
import json
import qrcode
from drivers.portal import *
from commands.multisig import get_cold_key_signature
from chia.wallet.lineage_proof import LineageProof
from commands.deployment import print_spend_instructions

PORTAL_COIN_ID_SAVE_FILE = "last_spent_portal_coinid"

async def get_latest_portal_coin_data(node: FullNodeRpcClient) -> Tuple[
    CoinSpend,
    bytes32,
    List[Tuple[bytes, bytes]],
    LineageProof
]:
    last_coin_id: bytes32
    try:
        last_coin_id = bytes.fromhex(open(PORTAL_COIN_ID_SAVE_FILE, "r").read())
    except:
        last_coin_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))

    parent_record = None
    coin_record: CoinRecord = await node.get_coin_record_by_name(last_coin_id)
    while coin_record.spent_block_index != 0:
        cs: CoinSpend = await node.get_puzzle_and_solution(
            coin_record.coin.name(), coin_record.spent_block_index
        )
        
        new_coins = compute_additions(cs)
        new_coin: Coin
        for c in new_coins:
            if c.amount % 2 == 1:
                new_coin = c
                break

        parent_record = coin_record
        coin_record: CoinRecord = await node.get_coin_record_by_name(new_coin.name())

    open(PORTAL_COIN_ID_SAVE_FILE, "w").write(coin_record.coin.parent_coin_info.hex())

    last_used_chains_and_nonces: List[Tuple[bytes, bytes]] = []
    inner_solution: Program = Program.from_bytes(bytes(cs.solution)).at("rrf")
    update_package = inner_solution.at("f")
    if bytes(update_package) == bytes(Program.to(0)):
        chains_and_nonces = inner_solution.at("rf").as_iter()
        for cn in chains_and_nonces:
            source_chain = cn.first().as_atom()
            nonce = cn.rest().as_atom()
            last_used_chains_and_nonces.append(
                (source_chain, nonce)
            )

    return parent_record, coin_record.coin.name(), last_used_chains_and_nonces, lineage_proof_for_coinsol(cs)


@click.group()
def rekey():
    pass


@rekey.command()
@click.option('--new-message-keys', required=True, help='New set of hot keys, separated by commas')
@click.option('--new-message-threshold', required=True, help='New threshold required for messages')
@click.option('--new-update-keys', required=True, help='New set of cold keys, separated by commas')
@click.option('--new-update-threshold', required=True, help='New threshold required for updates')
@click.option('--validator-index', required=True, help='Your validator index')
@click.option('--use-debug-method', is_flag=True, default=False, help='Use debug signing method')
def sign_tx(
    new_message_keys: str,
    new_message_threshold: int,
    new_update_keys: str,
    new_update_threshold: int,
    validator_index: int,
    use_debug_method: bool
):
    new_message_keys: List[G1Element] = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_message_keys.split(',')]
    new_update_keys: List[G1Element] = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_update_keys.split(',')]
    new_message_threshold = int(new_message_threshold)
    new_update_threshold = int(new_update_threshold)

    portal_launcher_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))

    current_message_threshold = int(get_config_item(["xch", "portal_threshold"]))
    current_message_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "portal_keys"])]
    current_update_threshold = int(get_config_item(["xch", "multisig_threshold"]))
    current_update_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "multisig_keys"])]

    updater_delegated_puzzle = get_portal_rekey_delegated_puzzle(
        portal_launcher_id,
        current_message_threshold,
        current_message_keys,
        new_message_threshold,
        new_message_keys,
        current_update_threshold,
        current_update_keys,
        new_update_threshold,
        new_update_keys
    )
    message_to_sign = updater_delegated_puzzle.get_tree_hash()

    get_cold_key_signature(message_to_sign, validator_index, use_debug_method)


@rekey.command()
@click.option('--new-message-keys', required=True, help='New set of hot keys, separated by commas')
@click.option('--new-message-threshold', required=True, help='New threshold required for messages')
@click.option('--new-update-keys', required=True, help='New set of cold keys, separated by commas')
@click.option('--new-update-threshold', required=True, help='New threshold required for updates')
@click.option('--sigs', required=True, help='Signature list - in the form: list of {validator_index-sig}, elements separated by comma (,)')
@click.option('--offer', default="help", help='Offer to build a multisig from (must offer  exactly 1 mojo + include min network fee)')
@async_func
@with_node
async def broadcast_spend(
    new_message_keys: str,
    new_message_threshold: int,
    new_update_keys: str,
    new_update_threshold: int,
    sigs: str,
    node: FullNodeRpcClient,
    offer: str
):
    if offer == "help":
        click.echo("Oops, you forgot --offer!")
        click.echo('chia rpc wallet create_offer_for_ids \'{"offer":{"1":-1},"fee":4200000000,"driver_dict":{},"validate_only":false}\'')
        return
    offer: Offer = Offer.from_bech32(offer)

    new_message_keys: List[G1Element] = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_message_keys.split(',')]
    new_update_keys: List[G1Element] = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_update_keys.split(',')]
    new_message_threshold = int(new_message_threshold)
    new_update_threshold = int(new_update_threshold)

    signature_validator_indexes = [int(sig.split('-')[0]) for sig in sigs.split(',')]
    actual_signature = [G2Element.from_bytes(bytes.fromhex(sig.split('-')[1])) for sig in sigs.split(',')]

    portal_launcher_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))

    current_message_threshold = int(get_config_item(["xch", "portal_threshold"]))
    current_message_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "portal_keys"])]
    current_update_threshold = int(get_config_item(["xch", "multisig_threshold"]))
    current_update_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "multisig_keys"])]

    parent_record, coin_id, last_used_chains_and_nonces, lineage_proof = await get_latest_portal_coin_data(node)
    print(f"Latest portal coin id: {coin_id.hex()}")

    offer_sb: SpendBundle = offer.to_spend_bundle()
    coin_spends = list(offer_sb.coin_spends)

    # identify source coin from offer
    source_xch_coin = None
    for coin_spend in offer_sb.coin_spends:
        cond_dict = conditions_dict_for_solution(
            coin_spend.puzzle_reveal,
            coin_spend.solution,
            INFINITE_COST
        )
        create_coins = cond_dict[ConditionOpcode.CREATE_COIN]

        for cc_cond in create_coins:
            if cc_cond.vars[0] == OFFER_MOD_HASH and cc_cond.vars[1] == b'\x01':
                source_xch_coin = Coin(coin_spend.coin.name(), OFFER_MOD_HASH, 1)
                break

    assert source_xch_coin is not None
    
    # spend source coin
    security_coin_puzzle = Program.to((1, [
        [ConditionOpcode.RESERVE_FEE, 1],
        [ConditionOpcode.ASSERT_CONCURRENT_SPEND, coin_id]
    ]))
    security_coin_puzzle_hash = security_coin_puzzle.get_tree_hash()

    source_coin_solution = Program.to([
        [source_xch_coin.name(), [security_coin_puzzle_hash, 1]],
    ])

    source_coin_spend = CoinSpend(
        source_xch_coin,
        OFFER_MOD,
        source_coin_solution
    )
    coin_spends.append(source_coin_spend)

    # spend security coin
    security_coin = Coin(source_xch_coin.name(), security_coin_puzzle_hash, 1)

    security_coin_spend = Program.to([])

    security_coin_spend = CoinSpend(
        security_coin,
        security_coin_puzzle,
        security_coin_spend
    )
    coin_spends.append(security_coin_spend)

    # spend portal
    updater_delegated_puzzle = get_portal_rekey_delegated_puzzle(
        portal_launcher_id,
        current_message_threshold,
        current_message_keys,
        new_message_threshold,
        new_message_keys,
        current_update_threshold,
        current_update_keys,
        new_update_threshold,
        new_update_keys
    )
    updater_delegated_puzzle_hash = updater_delegated_puzzle.get_tree_hash()

    updater_delegated_solution = get_portal_rekey_delegated_solution(
        last_used_chains_and_nonces
    )

    portal_updater_puzzle = get_multisig_inner_puzzle(
        current_update_keys,
        current_update_threshold,
    )

    selectors = [False for _ in range(current_update_threshold)]
    for sig_idx in signature_validator_indexes:
        selectors[sig_idx] = True
    portal_updater_solution = get_multisig_inner_solution(
        current_update_threshold,
        selectors,
        updater_delegated_puzzle_hash,
        updater_delegated_solution
    )

    portal_puzzle = get_portal_receiver_full_puzzle(
        portal_launcher_id,
        current_message_threshold,
        current_message_keys,
        portal_updater_puzzle.get_tree_hash(),
        last_used_chains_and_nonces
    )
    portal_puzzle_hash = portal_puzzle.get_tree_hash()

    portal_inner_solution = get_portal_receiver_inner_solution(
        [],
        portal_updater_puzzle,
        portal_updater_solution
    )
    portal_solution = solution_for_singleton(
        lineage_proof,
        1,
        portal_inner_solution
    )

    portal_coin = Coin(parent_record.coin.name(), portal_puzzle_hash, 1)
    portal_coin_spend = CoinSpend(
        portal_coin,
        portal_puzzle,
        portal_solution
    )
    coin_spends.append(portal_coin_spend)

    # finally, build spend bundle
    sb = SpendBundle(
        coin_spends,
        AugSchemeMPL.aggregate(
            [
                offer_sb.aggregated_signature
            ] + actual_signature
        )
    )
    print_spend_instructions(sb, coin_id)
