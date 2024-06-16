import click
import requests
from commands.cli_wrappers import *
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.bech32m import bech32_encode, convertbits, bech32_decode
from chia.types.coin_spend import CoinSpend
from chia.util.condition_tools import conditions_dict_for_solution
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from chia.wallet.trading.offer import OFFER_MOD
from chia.wallet.trading.offer import OFFER_MOD_HASH
from chia_rs import AugSchemeMPL, G1Element, G2Element
from chia.types.spend_bundle import SpendBundle
from chia.types.blockchain_format.program import INFINITE_COST
from nostr_sdk import Client, Filter, SingleLetterTag, Alphabet
from drivers.multisig import *
from drivers.portal import *
from commands.deployment import print_spend_instructions
from commands.rekey import get_latest_portal_coin_data
from datetime import timedelta


@click.group()
def ffs():
    pass

@ffs.command()
@click.option('--nonce', required=True, help='Message nonce')
@click.option('--source-chain', required=True, help='Message source chain')
@click.option('--watcher-api-url', default="https://watcher-api.warp.green/", help='Wather API base URL')
@click.option('--offer', default="help", help='Offer to use as fee source (must offer  exactly 1 mojo + include min network fee)')
@async_func
@with_node
async def partial_relay_message(
    nonce: str,
    source_chain: str,
    watcher_api_url: str,
    node: FullNodeRpcClient,
    offer: str
):
    if offer == "help":
        click.echo("Oops, you forgot --offer!")
        click.echo('chia rpc wallet create_offer_for_ids \'{"offer":{"1":-1},"fee":420000000,"driver_dict":{},"validate_only":false}\'')
        return
    offer: Offer = Offer.from_bech32(offer)

    nonce = nonce.replace("0x", "")

    print("Getting message data...")
    r = requests.get(f"{watcher_api_url}messages?source_chain={source_chain}&nonce={nonce}")
    msgs = r.json()
    if len(msgs) == 0:
        print("Message not found :(")
        return
    if len(msgs) > 1:
        print("Too many messages found - this is strange...")
        return
    
    msg = msgs[0]
    assert msg['status'] == 'sent'

    print("Syncing portal...")
    portal_launcher_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))

    current_message_threshold = int(get_config_item(["xch", "portal_threshold"]))
    current_message_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "portal_keys"])]
    current_update_threshold = int(get_config_item(["xch", "multisig_threshold"]))
    current_update_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "multisig_keys"])]

    parent_record, coin_id, last_used_chains_and_nonces, lineage_proof = await get_latest_portal_coin_data(node)
    print(f"Latest portal coin id: {coin_id.hex()}")

    print("Getting validator sigs...")
    validator_sigs = []
    validator_sig_switches = [False for _ in current_message_keys]

    client = Client(None)

    try:
      nostr_pubkeys = get_config_item(["nostr", "pubkeys"])
    except:
      print("nostr.pubkeys not defined in config :(")
      return

    relays = get_config_item(["nostr", "relays"])
    client.add_relays(relays)
    client.connect()

    filter = Filter().custom_tag(
        SingleLetterTag.lowercase(Alphabet.R), [
            bech32_encode("r", convertbits(source_chain.encode() + msg['destination_chain'].encode() + bytes.fromhex(nonce), 8, 5))
        ]
    ).custom_tag(
        SingleLetterTag.lowercase(Alphabet.C), [
            bech32_encode("c", convertbits(coin_id, 8, 5))
        ]
    )
    
    while len(validator_sigs) < current_message_threshold:
        events = client.get_events_of([filter], timedelta(seconds=5))
        print(f"Got {len(events)} events")
        for event in events:
            event_author = event.author().to_hex().replace("0x", "")
            if event_author not in nostr_pubkeys:
                print(f"Skipping event {event} because author is not in pubkeys...")
                continue
            
            validator_index = -1
            for i, pk in enumerate(nostr_pubkeys):
                if event_author == pk:
                    validator_index = i
                    break
                
            if validator_sig_switches[validator_index] == False:
              sig = event.content()
              sig = bytes(convertbits(bech32_decode(sig, 96 * 2)[1], 5, 8, False))
              sig = G2Element.from_bytes(sig)
              validator_sigs.append(sig)
              validator_sig_switches[validator_index] = True
            
    print("Disconnecting from relays...")
    client.disconnect()

    print("Building spend...")
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
        create_coins = cond_dict.get(ConditionOpcode.CREATE_COIN, [])

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
    portal_updater_puzzle = get_multisig_inner_puzzle(
        current_update_keys,
        current_update_threshold,
    )

    portal_inner_puzzle = get_portal_receiver_inner_puzzle(
        portal_launcher_id,
        current_message_threshold,
        current_message_keys,
        portal_updater_puzzle.get_tree_hash(),
        last_used_chains_and_nonces
    )

    portal_puzzle = puzzle_for_singleton(portal_launcher_id, portal_inner_puzzle)
    portal_puzzle_hash = portal_puzzle.get_tree_hash()

    portal_msg = PortalMessage(
        nonce=bytes.fromhex(nonce),
        validator_sig_switches=validator_sig_switches,
        source_chain=source_chain.encode(),
        source=bytes.fromhex(msg['source']),
        destination=bytes.fromhex(msg['destination']),
        message=Program.to([bytes.fromhex(_) for _ in msg['contents']])
    )
    portal_inner_solution = get_portal_receiver_inner_solution(
        [portal_msg],
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
            ] + validator_sigs
        )
    )
    print_spend_instructions(sb, coin_id)
