import click
from commands.cli_wrappers import *
from chia.wallet.puzzles.singleton_top_layer_v1_1 import pay_to_singleton_puzzle
from chia.wallet.puzzles.singleton_top_layer_v1_1 import claim_p2_singleton, pay_to_singleton_puzzle
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_record import CoinRecord
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.types.coin_spend import CoinSpend, compute_additions
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.coin import Coin
from commands.keys import mnemonic_to_validator_pk
from chia.util.bech32m import decode_puzzle_hash
from typing import List
from chia_rs import PrivateKey, AugSchemeMPL, G1Element, G2Element
from chia.types.spend_bundle import SpendBundle
from chia.types.blockchain_format.program import INFINITE_COST
from chia.util.condition_tools import conditions_dict_for_solution
from chia.wallet.trading.offer import OFFER_MOD
from chia.wallet.trading.offer import OFFER_MOD_HASH
from typing import Tuple
from drivers.multisig import *
import json
import qrcode
from commands.deployment import print_spend_instructions

@click.group()
def multisig():
    pass

COIN_ID_SAVE_FILE = "last_spent_multisig_coinid"

async def get_latest_multisig_coin_spend_and_new_id(node: FullNodeRpcClient) -> Tuple[CoinSpend, bytes32]:
    last_coin_id: bytes32
    try:
        last_coin_id = bytes.fromhex(open(COIN_ID_SAVE_FILE, "r").read())
    except:
        last_coin_id = bytes.fromhex(get_config_item(["xch", "multisig_launcher_id"]))

    parent_record = None
    coin_record: CoinRecord = await node.get_coin_record_by_name(last_coin_id)
    cs: CoinSpend
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

    open(COIN_ID_SAVE_FILE, "w").write(coin_record.coin.parent_coin_info.hex())
    return cs, coin_record.coin.name()


def get_delegated_puzzle_for_unsigned_tx(unsigned_tx) -> Program:
    launcher_id: bytes32 = bytes.fromhex(get_config_item(["xch", "multisig_launcher_id"]))
    coin_id: bytes32 = bytes.fromhex(unsigned_tx["multisig_latest_id"])
    conditions = []

    p2_ph = pay_to_singleton_puzzle(launcher_id).get_tree_hash()
    p2_coin_ids: List[bytes32] = [
        Coin(
            bytes.fromhex(coin_parent),
            p2_ph,
            coin_amount
        ).name() for coin_parent, coin_amount in unsigned_tx["coins"].items()
    ]

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

    for p2_coin_id in p2_coin_ids:
        conditions.append(Program.to([
            ConditionOpcode.CREATE_PUZZLE_ANNOUNCEMENT,
            p2_coin_id
        ]))

    conditions.append([ConditionOpcode.RESERVE_FEE, fee])

    inner_puzzle: Program = get_multisig_inner_puzzle(
        [G1Element.from_bytes(bytes.fromhex(pk_str)) for pk_str in get_config_item(["xch", "multisig_keys"])],
        get_config_item(["xch", "multisig_threshold"])
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
async def start_new_payout_tx(
    node: FullNodeRpcClient,
    payout_structure_file: str
):
    click.echo("Finding coins...")
    launcher_id = get_config_item(["xch", "multisig_launcher_id"])
    launcher_id: bytes32 = bytes.fromhex(launcher_id)
    p2_puzzle_hash = pay_to_singleton_puzzle(launcher_id).get_tree_hash()
    click.echo(f"p2_puzzle_hash: {p2_puzzle_hash.hex()}")

    p2_puzzle_hash_verify = get_config_item(["xch", "bridging_ph"])
    if p2_puzzle_hash.hex() != p2_puzzle_hash_verify:
        click.echo("Oops! p2_puzzle_hash mismatch :(")
        return

    coin_records: List[CoinRecord] = await node.get_coin_records_by_puzzle_hash(p2_puzzle_hash, include_spent_coins=False)

    # in case someone tries to be funny
    # filter_amount = get_config_item(["xch", "per_message_fee"])
    # coin_records = [cr for cr in coin_records if cr.coin.amount == filter_amount]

    if len(coin_records) == 0:
        click.echo("No claimable coins found :(")
        return
    
    click.echo("Found coins:")
    for cr in coin_records:
        click.echo(f"{cr.coin.name().hex()} -> {cr.coin.amount / 10 ** 12} XCH")

    click.echo(f"In total, there are {len(coin_records)} coins with a total value of {sum([cr.coin.amount for cr in coin_records]) / 10 ** 12} XCH.")

    value = input("In XCH, how much would you like to collect? ")
    value = int(float(value) * 10 ** 12)

    fee = input("In XCH, what fee should the claim tx have? ")
    fee = int(float(fee) * 10 ** 12)

    total_value = value + fee
    click.echo(f"Total value: {total_value / (10 ** 12)} XCH ({total_value} mojos)")

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
        coins_to_claim[cr.coin.parent_coin_info.hex()] = cr.coin.amount
    open("unsigned_tx.json", "w").write(json.dumps({
        "payout_structure": payout_structure,
        "coins": coins_to_claim,
        "fee": fee,
        "multisig_latest_id": latest_id.hex()
    }, indent=4))
    click.echo("Done! Unsigned transaction saved to unsigned_tx.json")

    node.close()
    await node.await_closed()


@multisig.command()
@click.option('--unsigned-tx-file', required=True, help='JSON file containing unsigned tx details')
@click.option('--validator-index', required=True, help='Your validator index')
@click.option('--use-debug-method', is_flag=True, default=False, help='Use debug signing method')
def sign_payout_tx(
    unsigned_tx_file: str,
    validator_index: int,
    use_debug_method: bool
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
        click.echo(f"{address}: {mojo_amount / 10 ** 12} XCH ({mojo_amount} mojos - {share * 10000 // total_share / 100}%)")
    
    message_to_sign: bytes32 = get_delegated_puzzle_for_unsigned_tx(unsigned_tx).get_tree_hash()

    click.echo(f"The claim transaction will also include a fee of {fee // 10 ** 12} XCH ({fee} mojos).")
    get_cold_key_signature(message_to_sign, int(validator_index), use_debug_method)
        

@multisig.command()
@click.option('--unsigned-tx-file', required=True, help='JSON file containing unsigned tx details')
@click.option('--sigs', required=True, help='Signatures, separated by commas')
@async_func
@with_node
async def broadcast_payout_spend(
    node: FullNodeRpcClient,
    unsigned_tx_file: str,
    sigs: str
):
  click.echo("Building spend bundle...")
  unsigned_tx = json.loads(open(unsigned_tx_file, "r").read())

  coin_id = bytes.fromhex(unsigned_tx["multisig_latest_id"])
  multisig_record: CoinRecord = await node.get_coin_record_by_name(coin_id)
  assert multisig_record.spent_block_index == 0

  multisig_parent_spend = await node.get_puzzle_and_solution(
      multisig_record.coin.parent_coin_info,
      multisig_record.confirmed_block_index
  )

  delegated_puzzle = get_delegated_puzzle_for_unsigned_tx(unsigned_tx)
  delegated_puzzle_hash: bytes32 = delegated_puzzle.get_tree_hash()
  multisig_launcher_id = bytes.fromhex(get_config_item(["xch", "multisig_launcher_id"]))

  threshold = get_config_item(["xch", "multisig_threshold"])
  pks = [G1Element.from_bytes(bytes.fromhex(pk_str)) for pk_str in get_config_item(["xch", "multisig_keys"])]

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

  selectors = [0 for _ in pks]
  parsed_sigs: List[G2Element] = []
  for sig in sigs.split(","):
      pk_index, sig = sig.split("-")
      pk_index = int(pk_index)
      sig = G2Element.from_bytes(bytes.fromhex(sig))
      click.echo(f"Verifying signature {pk_index}-{sig}...")
      if not AugSchemeMPL.verify(pks[pk_index], delegated_puzzle_hash, sig):
          click.echo("Invalid signature :(")
          return

      selectors[pk_index] = 1
      parsed_sigs.append(sig)

  multisig_solution = get_multisig_solution(
      multisig_parent_spend,
      threshold,
      selectors,
      delegated_puzzle
  )

  multisig_coin_spend = CoinSpend(multisig_coin, multisig_puzzle, multisig_solution)
  coin_spends = [ multisig_coin_spend ]

  p2_puzzle_hash = pay_to_singleton_puzzle(multisig_launcher_id).get_tree_hash()
  for coin_parent, coin_amount in unsigned_tx["coins"].items():
      coin = Coin(bytes.fromhex(coin_parent), p2_puzzle_hash, coin_amount)
      coin_record: CoinRecord = await node.get_coin_record_by_name(
        coin.name()
      )
      _, _, spend = claim_p2_singleton(
          coin_record.coin, multisig_inner_puzzle_hash, multisig_launcher_id
      )
      coin_spends.append(spend)

  spend_bundle = SpendBundle(coin_spends, AugSchemeMPL.aggregate(parsed_sigs))
  print_spend_instructions(spend_bundle, multisig_coin_spend.coin.name())


def get_cold_key_signature(
        message_to_sign: bytes32,
        validator_index: int,
        use_debug_method: bool
):
    click.echo(f"Message to sign: {message_to_sign.hex()}")
    click.echo(f"Your validator index: {validator_index}")

    if use_debug_method:
        # this was the previous mechanism
        # since then, cold keys moved to hardware wallets!
        mnemo = input("To sign, input your 12-word cold mnemonic: ")
        sk = mnemonic_to_validator_pk(mnemo.strip())
        sig = AugSchemeMPL.sign(sk, message_to_sign)
        click.echo(f"Signature: {validator_index}-{bytes(sig).hex()}")
        return

    j = {"validator_index": validator_index, "message": message_to_sign.hex()}
    j_str = json.dumps(j)
    click.echo(f"QR code data: {j_str}")

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(j_str)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save("qr.png")
    qr.print_tty()
    click.echo("QR code also saved to qr.png")


def get_rekey_delegated_puzzle(
        current_coin_name: bytes32,
        multisig_launcher_id: bytes32,
        current_threshold: int,
        current_keys: List[G1Element],
        new_threshold: int,
        new_keys: List[G1Element]
) -> Program:
    current_inner_puzzle = get_multisig_inner_puzzle(current_keys, current_threshold)
    new_inner_puzzle = get_multisig_inner_puzzle(new_keys, new_threshold)

    current_puzzle = puzzle_for_singleton(
        multisig_launcher_id,
        current_inner_puzzle
    )
    
    return Program.to((1, [
        [ConditionOpcode.ASSERT_MY_COIN_ID, current_coin_name],
        [ConditionOpcode.CREATE_COIN, new_inner_puzzle.get_tree_hash(), 1],
        [ConditionOpcode.ASSERT_MY_PUZZLEHASH, current_puzzle.get_tree_hash()] # just to make sure data is up-to-date
    ]))


@multisig.command()
@click.option('--new-keys', required=True, help='New set of cold keys, separated by commas')
@click.option('--new-threshold', required=True, help='New signature threshold required for transactions')
@click.option('--validator-index', required=True, help='Your validator index')
@click.option('--use-debug-method', is_flag=True, default=False, help='Use debug signing method')
@async_func
@with_node
async def sign_rekey_tx(
    new_keys: str,
    new_threshold: int,
    validator_index: int,
    use_debug_method: bool,
    node: FullNodeRpcClient
):
    new_threshold = int(new_threshold)
    new_keys = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_keys.split(",")]

    click.echo("Finding latest multisig coin spend...")
    _, coin_id = await get_latest_multisig_coin_spend_and_new_id(node)
    click.echo(f"Latest multisig coin: {coin_id.hex()}")

    current_multisig_threshold = int(get_config_item(["xch", "multisig_threshold"]))
    current_multisig_keys = [G1Element.from_bytes(bytes.fromhex(key)) for key in get_config_item(["xch", "multisig_keys"])]

    multisig_launcher_id = bytes.fromhex(get_config_item(["xch", "multisig_launcher_id"]))

    message_to_sign: bytes32 = get_rekey_delegated_puzzle(
        coin_id,
        multisig_launcher_id,
        current_multisig_threshold,
        current_multisig_keys,
        new_threshold,
        new_keys
    ).get_tree_hash()

    get_cold_key_signature(message_to_sign, int(validator_index), use_debug_method)


@multisig.command()
@click.option('--new-keys', required=True, help='New set of cold keys, separated by commas')
@click.option('--new-threshold', required=True, help='New signature threshold required for transactions')
@click.option('--sigs', required=True, help='Signatures, separated by commas')
@click.option('--offer', default="help", help='Offer to use as fee soruce (must offer  exactly 1 mojo + include min network fee)')
@async_func
@with_node
async def broadcast_rekey_spend(
    new_keys: str,
    new_threshold: int,
    sigs: str,
    node: FullNodeRpcClient,
    offer: str
):
    if offer == "help":
        click.echo("Oops, you forgot --offer!")
        click.echo('chia rpc wallet create_offer_for_ids \'{"offer":{"1":-1},"fee":4200000000,"driver_dict":{},"validate_only":false}\'')
        return
    offer: Offer = Offer.from_bech32(offer)

    new_threshold = int(new_threshold)
    new_keys = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_keys.split(",")]

    click.echo("Finding latest multisig coin spend...")
    parent_spend, coin_id = await get_latest_multisig_coin_spend_and_new_id(node)
    click.echo(f"Latest multisig coin: {coin_id.hex()}")

    current_multisig_threshold = int(get_config_item(["xch", "multisig_threshold"]))
    current_multisig_keys = [G1Element.from_bytes(bytes.fromhex(key)) for key in get_config_item(["xch", "multisig_keys"])]
    multisig_launcher_id: bytes32 = bytes.fromhex(get_config_item(["xch", "multisig_launcher_id"]))

    sig_indexes = [int(sig.split("-")[0]) for sig in sigs.split(",")]
    parsed_sigs = [G2Element.from_bytes(bytes.fromhex(sig.split("-")[1])) for sig in sigs.split(",")]

    # 1. Locate xch source, spend it
    offer_sb: SpendBundle = offer.to_spend_bundle()
    coin_spends = offer_sb.coin_spends
    
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

    security_coin_puzzle = Program.to((1, [
        [ConditionOpcode.RESERVE_FEE, 1],
        [ConditionOpcode.ASSERT_CONCURRENT_SPEND, coin_id]
    ]))
    security_coin_puzzle_hash = security_coin_puzzle.get_tree_hash()

    source_xch_coin_solution = Program.to([
        [source_xch_coin.name(), [security_coin_puzzle_hash, 1]]
    ])

    source_xch_coin_spend = CoinSpend(
        source_xch_coin,
        OFFER_MOD,
        source_xch_coin_solution
    )
    coin_spends.append(source_xch_coin_spend)

    # 2. Spend security coin
    security_coin = Coin(source_xch_coin.name(), security_coin_puzzle_hash, 1)

    security_coin_solution = Program.to([])

    security_coin_spend = CoinSpend(security_coin, security_coin_puzzle, security_coin_solution)
    coin_spends.append(security_coin_spend)

    # 3. Spend multisig

    current_inner_puzzle = get_multisig_inner_puzzle(current_multisig_keys, current_multisig_threshold)
    current_puzzle = puzzle_for_singleton(
        multisig_launcher_id,
        current_inner_puzzle
    )

    multisig_coin = Coin(parent_spend.coin.name(), current_puzzle.get_tree_hash(), 1)

    delegated_puzzle = get_rekey_delegated_puzzle(
        coin_id,
        multisig_launcher_id,
        current_multisig_threshold,
        current_multisig_keys,
        new_threshold,
        new_keys
    )

    selectors = [False for _ in current_multisig_keys]
    for i in sig_indexes:
        selectors[i] = True
    multisig_solution = get_multisig_solution(
        parent_spend,
        current_multisig_threshold,
        selectors,
        delegated_puzzle,
        Program.to([])
    )

    multisig_spend = CoinSpend(multisig_coin, current_puzzle, multisig_solution)
    coin_spends.append(multisig_spend)

    # 5. Build spend bundle

    spend_bundle = SpendBundle(coin_spends, AugSchemeMPL.aggregate(
        parsed_sigs + [
            offer_sb.aggregated_signature
        ]
    ))
    print_spend_instructions(spend_bundle, multisig_coin.name())
