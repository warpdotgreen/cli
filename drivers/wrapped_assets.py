from drivers.utils import load_clvm_hex, raw_hash
from drivers.portal import get_message_coin_puzzle_1st_curry
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH
from chia.types.blockchain_format.coin import Coin

WRAPPED_TAIL_MOD = load_clvm_hex("puzzles/wrapped_assets/wrapped_tail.clsp")
WRAPPED_TAIL_MOD_HASH = WRAPPED_TAIL_MOD.get_tree_hash()

CAT_MINTER_MOD = load_clvm_hex("puzzles/wrapped_assets/cat_minter.clsp")

CAT_MINT_AND_PAYOUT_MOD = load_clvm_hex("puzzles/wrapped_assets/cat_mint_and_payout.clsp")
CAT_MINT_AND_PAYOUT_MOD_HASH = CAT_MINT_AND_PAYOUT_MOD.get_tree_hash()

CAT_BURNER_MOD = load_clvm_hex("puzzles/wrapped_assets/cat_burner.clsp")

BURN_INNER_PUZZLE_MOD = load_clvm_hex("puzzles/wrapped_assets/burn_inner_puzzle.clsp")
BURN_INNER_PUZZLE_MOD_HASH = BURN_INNER_PUZZLE_MOD.get_tree_hash()

def get_cat_burner_puzzle(
    bridging_puzzle_hash: bytes32,
    eth_token_bridge_address: bytes,
) -> Program:
  return CAT_BURNER_MOD.curry(
    CAT_MOD_HASH,
    BURN_INNER_PUZZLE_MOD_HASH,
    bridging_puzzle_hash,
    eth_token_bridge_address
  )

def get_cat_minter_puzzle(
    portal_receiver_launcher_id: bytes32,
    bridging_puzzle_hash: bytes32,
    eth_token_bridge_address: bytes,
) -> Program:
  return CAT_MINTER_MOD.curry(
    get_message_coin_puzzle_1st_curry(portal_receiver_launcher_id).get_tree_hash(),
    CAT_MOD_HASH,
    WRAPPED_TAIL_MOD_HASH,
    CAT_MINT_AND_PAYOUT_MOD_HASH,
    raw_hash([
      b'\x01',
      get_cat_burner_puzzle(bridging_puzzle_hash, eth_token_bridge_address).get_tree_hash()
    ]), # CAT_BURNER_PUZZLE_HASH_HASH = (sha256 1 CAT_BURNER_PUZZLE_HASH_HASH)
    BURN_INNER_PUZZLE_MOD_HASH,
    eth_token_bridge_address
  )

def get_cat_mint_and_payout_inner_puzzle(
    receiver: bytes32
) -> Program:
  return CAT_MINT_AND_PAYOUT_MOD.curry(
    receiver
  )

def get_cat_burn_inner_puzzle_self_hash(
    bridging_puzzle_hash: bytes32,
    eth_token_bridge_address: bytes,
    eth_erc20_address: bytes,
) -> bytes32:
  return BURN_INNER_PUZZLE_MOD.curry(
    get_cat_burner_puzzle(bridging_puzzle_hash, eth_token_bridge_address).get_tree_hash(),
    eth_erc20_address
  ).get_tree_hash()

def get_cat_brun_inner_puzzle(
    bridging_puzzle_hash: bytes32,
    eth_token_bridge_address: bytes,
    eth_erc20_address: bytes,
    target_receiver: bytes,
) -> Program:
  first_curry = BURN_INNER_PUZZLE_MOD.curry(
    get_cat_burner_puzzle(bridging_puzzle_hash, eth_token_bridge_address).get_tree_hash(),
    eth_erc20_address
  )

  return first_curry.curry(
    first_curry.get_tree_hash(),
    target_receiver
  )

def get_wrapped_tail(
    portal_receiver_launcher_id: bytes32,
    bridging_puzzle_hash: bytes32,
    eth_token_bridge_address: bytes,
    eth_erc20_address: bytes,
) -> Program:
  return WRAPPED_TAIL_MOD.curry(
    get_cat_minter_puzzle(portal_receiver_launcher_id, bridging_puzzle_hash, eth_token_bridge_address).get_tree_hash(),
    get_cat_burn_inner_puzzle_self_hash(bridging_puzzle_hash, eth_token_bridge_address, eth_erc20_address)
  )

def get_burn_inner_puzzle_solution(
    cat_burner_parent_id: bytes32,
    cat_burner_amount: int,
    my_coin_id: bytes32,
) -> Program:
  return Program.to([
    cat_burner_parent_id,
    cat_burner_amount,
    my_coin_id
  ])

def get_cat_mint_and_payout_inner_puzzle_solution(
    tail_puzzle: Program,
    my_amount: int,
    parent_parent_info: bytes32,
) -> Program:
  return Program.to([
    tail_puzzle,
    my_amount,
    parent_parent_info
  ])

def get_cat_minter_puzzle_solution(
    deadline: int,
    message: Program,
    my_puzzle_hash: bytes32,
    my_coin_id: bytes32,
    message_coin_parent_info: bytes32,
) -> Program:
  return Program.to([
    deadline,
    message,
    my_puzzle_hash,
    my_coin_id,
    message_coin_parent_info
  ])

def get_cat_burner_puzzle_solution(
    cat_parent_info: bytes32,
    tail_hash: bytes32,
    cat_amount: int,
    eth_erc20_address: bytes,
    eth_receiver_address: bytes,
    time_now_ish: int,
    my_coin: Coin
) -> Program:
  return Program.to([
    cat_parent_info,
    raw_hash([b'\x01', tail_hash]),
    cat_amount,
    eth_erc20_address,
    eth_receiver_address,
    time_now_ish,
    my_coin.amount,
    my_coin.puzzle_hash,
    my_coin.name()
  ])
