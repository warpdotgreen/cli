import click
from commands.cli_wrappers import *
from chia.wallet.puzzles.singleton_top_layer_v1_1 import pay_to_singleton_puzzle
from chia.wallet.puzzles.singleton_top_layer_v1_1 import claim_p2_singleton
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_record import CoinRecord
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.types.coin_spend import CoinSpend, compute_additions
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from commands.keys import mnemonic_to_validator_pk
from chia.util.bech32m import decode_puzzle_hash
from typing import List
from blspy import PrivateKey, AugSchemeMPL, G1Element
from chia.types.spend_bundle import SpendBundle
from drivers.multisig import *
import json

@click.group()
def multisig():
    pass

COIN_ID_SAVE_FILE = "last_spent_multisig_coinid"

async def get_latest_multisig_coin_spend_and_new_id(node: FullNodeRpcClient) -> [CoinSpend, bytes32]:
    last_coin_id: bytes32
    try:
        last_coin_id = bytes.fromhex(open(COIN_ID_SAVE_FILE, "r").read())
    except:
        last_coin_id = get_config_item(["chia", "multisig_launcher_id"])

    parent_record = None
    coin_record: CoinRecord = await node.get_coin_record_by_name(last_coin_id)
    while coin_record.spent_block_index is not None:
        spend: CoinSpend = await node.get_puzzle_and_solution(
            coin_record.coin.name(), coin_record.spent_block_index
        )
        additions = compute_additions(spend.coin_spend)
        new_coin: Coin
        for c in additions:
            if c.amount % 2 == 1:
                new_coin = c
                break

        parent_record = coin_record
        coin_record = await node.get_coin_record_by_name(new_coin.name())

    open(COIN_ID_SAVE_FILE, "w").write(coin_record.coin.parent_coin_info.hex())
    return parent_record, coin_record.coin.name()


def get_delegated_puzzle_for_unsigned_tx(unsigned_tx) -> Program:
    coin_id: bytes32 = bytes.fromhex(unsigned_tx["multisig_latest_id"])
    conditions = []

    total_amount = sum([v for v in unsigned_tx["coins"].values()])
    fee = unsigned_tx["fee"]
    payout_amount = total_amount - fee

    payout_structure = unsigned_tx["payout_structure"]
    total_share = sum([v for v in payout_structure.values()])

    for address, share in payout_structure.items():
        mojo_amount = share * payout_amount // total_share
        if mojo_amount % 2 == 1:
            mojo_amount -= 1
            fee += 1
        
        conditions.append(Program.to([
            ConditionOpcode.CREATE_COIN,
            decode_puzzle_hash(address),
            mojo_amount
        ]))

    for coin_id in unsigned_tx["coins"].keys():
        conditions.append(Program.to([
            ConditionOpcode.CREATE_PUZZLE_ANNOUNCEMENT,
            bytes.fromhex(coin_id)
        ]))

    conditions.append([ConditionOpcode.RESERVE_FEE, fee])

    inner_puzzle: Program = get_multisig_inner_puzzle(
        [G1Element.from_bytes(bytes.fromhex(pk_str)) for pk_str in get_config_item("chia", "multisig_pks")],
        get_config_item("chia", "multisig_threshold")
    )
    delegated_puzzle: Program = get_multisig_delegated_puzzle_for_conditions(
        coin_id,
        inner_puzzle.get_tree_hash(),
        conditions
    )
    return delegated_puzzle

@multisig.command()
@click.option('--payout-structure-file', required=True, help='JSON file containing {address: share}')
@async_func
@with_node
async def start_new_tx(
    node: FullNodeRpcClient,
    payout_structure_file: str
):
    click.echo("Finding coins...")
    launcher_id = get_config_item(["chia", "multisig_launcher_id"])
    launcher_id: bytes32 = bytes.fromhex(launcher_id)
    p2_puzzle_hash = pay_to_singleton_puzzle(launcher_id).get_tree_hash()
    click.echo(f"p2_puzzle_hash: {p2_puzzle_hash.hex()}")

    p2_puzzle_hash_verify = get_config_item(["chia", "bridging_ph"])
    if p2_puzzle_hash.hex() != p2_puzzle_hash_verify:
        click.echo("Oops! p2_puzzle_hash mismatch :(")
        return

    coin_records: List[CoinRecord] = await node.get_coin_records_by_puzzle_hash(p2_puzzle_hash, include_spent_coins=False)

    # in case someone tries to be funny
    # filter_amount = get_config_item(["chia", "per_message_fee"])
    # coin_records = [cr for cr in coin_records if cr.coin.amount == filter_amount]

    if len(coin_records) == 0:
        click.echo("No claimable coins found :(")
        return
    
    click.echo("Found coins:")
    for cr in coin_records:
        click.echo(f"{cr.coin.name().hex()} -> {cr.coin.amount / 10 ** 12} XCH")

    click.echo(f"In total, there are {len(coin_records)} coins with a total value of {sum([cr.coin.amount for cr in coin_records]) / 10 ** 12} XCH.")

    value = input("In XCH, how much would you like to collect? ")
    value = int(float(value) ** 10 ** 12)

    fee = input("In XCH, what fee should the claim tx have? ")
    fee = int(float(value) ** 10 ** 12)

    total_value = value + fee

    max_coins = 200
    to_claim = []
    for cr in coin_records[:max_coins]:
        total_value -= cr.coin.amount
        to_claim.append(cr)
        if total_value < 0:
            break
        
    click.echo(f"Selected {len(to_claim)} claimable coins with a total value of {sum([cr.coin.amount for cr in to_claim]) / 10 ** 12} XCH.")
    
    payout_structure = json.loads(open(payout_structure_file, "r").read())
    click.echo("Payout structure data loaded.")

    click.echo("One last thing: finding latest multisig coin spend...")
    _, latest_id = await get_latest_multisig_coin_spend_and_new_id(node)

    click.echo(f"Latest spent multisig coin: {latest_id.hex()}")

    coins_to_claim = {}
    for cr in to_claim:
        coins_to_claim[cr.coin.name().hex()] = cr.coin.amount
    open("unsigned_tx.json", "w").write(json.dumps({
        "payout_structure": payout_structure,
        "coins": coins_to_claim,
        "fee": fee,
        "multisig_latest_id": latest_id.hex()
    }))
    click.echo("Done! Unsigned transaction saved to unsigned_tx.json")

    node.close()
    await node.await_closed()


@multisig.command()
@click.option('--unsigned-tx-file', required=True, help='JSON file containing unsigned tx details')
def sign_tx(
    unsigned_tx_file: str
):
    unsigned_tx = json.loads(open(unsigned_tx_file, "r").read())
    click.echo("Parsing unsigned tx...")

    total_amount = sum([v for v in unsigned_tx["coins"].values()])
    fee = unsigned_tx["fee"]
    payout_amount = total_amount - fee

    payout_structure = unsigned_tx["payout_structure"]
    total_share = sum([v for v in payout_structure.values()])

    click.echo("Payouts:")
    for address, share in payout_structure.items():
        mojo_amount = share * payout_amount // total_share
        if mojo_amount % 2 == 1:
            mojo_amount -= 1
            fee += 1
        click.echo(f"{address}: {mojo_amount // 10 ** 12} XCH ({mojo_amount} mojos - {share * 10000 // total_share / 100}%)")
    
    message_to_sign: bytes32 = get_delegated_puzzle_for_unsigned_tx(unsigned_tx).get_tree_hash()

    click.echo(f"The claim transaction will also include a fee of {fee // 10 ** 12} XCH ({fee} mojos).")
    mnemo = input("To sign, input your 12-word cold mnemonic: ")
    sk: PrivateKey = mnemonic_to_validator_pk(mnemo.strip())
    pk: G1Element = sk.get_g1()
    sig = AugSchemeMPL.sign(sk, message_to_sign)

    pks = get_config_item(["chia", "multisig_pks"])
    pk_index = pks.index(bytes(pk).hex())
    click.echo(f"Signature: {pk_index}-{bytes(sig).hex()}")

@multisig.command()
@click.option('--unsigned-tx-file', required=True, help='JSON file containing unsigned tx details')
@click.option('--sigs', required=True, help='Signatures, separated by commas')
@async_func
@with_node
async def start_new_tx(
    node: FullNodeRpcClient,
    payout_structure_file: str,
    sigs: str
):
  click.echo("Building spend bundle...")
  payout_structure = json.loads(open(payout_structure_file, "r").read())

  coin_id = payout_structure["multisig_latest_id"]
  multisig_record: CoinRecord = await node.get_coin_record_by_name(coin_id)
  assert multisig_record.spent_block_index is None

  multisig_parent_spend = await node.get_puzzle_and_solution(
      multisig_record.coin.parent_coin_info,
      multisig_record.confirmed_block_index
  )

  delegated_puzzle = get_delegated_puzzle_for_unsigned_tx(payout_structure)
  multisig_launcher_id = bytes.fromhex(get_config_item(["chia", "multisig_launcher_id"]))

  threshold = get_config_item("chia", "multisig_threshold")
  pks = [G1Element.from_bytes(bytes.fromhex(pk_str)) for pk_str in get_config_item("chia", "multisig_pks")]

  multisig_inner_puzzle = get_multisig_inner_puzzle(
      pks,
      threshold
  )
  multisig_inner_puzzle_hash = multisig_inner_puzzle.get_tree_hash()

  multisig_puzzle = puzzle_for_singleton(
      multisig_launcher_id,
      multisig_inner_puzzle
  )
  multisig_coin = Coin(multisig_parent_spend.coin.name(), multisig_puzzle.get_tree_hash(), 1)

  selectors = [0 for _ in range(pks)]
  sigs = []
  for sig in sigs.split(","):
      pk_index, sig = sig.split("-")
      pk_index = int(pk_index)
      sig = bytes.fromhex(sig)
      click.print(f"Verifying signature {pk_index}-{sig}...")
      if not AugSchemeMPL.verify(pks[pk_index], multisig_inner_puzzle_hash, sig):
          click.echo("Invalid signature :(")
          return

      selectors[pk_index] = 1
      sigs.append(sig)

  multisig_solution = get_multisig_solution(
      multisig_parent_spend,
      get_config_item("chia", "multisig_threshold"),
      selectors,
      delegated_puzzle
  )

  multisig_coin_spend = CoinSpend(multisig_coin, multisig_puzzle, multisig_solution)
  coin_spends = [ multisig_coin_spend ]

  for coin_id in payout_structure["coins"].keys():
      coin_record: CoinRecord = await node.get_coin_record_by_name(coin_id)
      _, _, spend = claim_p2_singleton(
          coin_record.coin, multisig_inner_puzzle_hash, multisig_launcher_id
      )
      coin_spends.append(spend)

  spend_bundle = SpendBundle(coin_spends, sigs)
  open("sb.json", "w").write(json.dumps(spend_bundle.to_json_dict(), indent=4))
  open("push_request.json", "w").write(json.dumps({"spend_bundle": spend_bundle.to_json_dict()}, indent=4))
  click.echo("Spend bundle constructed and saved to sb.json.")
  click.echo("To send, use: chia rpc full_node push_tx -f push_request.json")

  node.close()
  await node.await_closed()
