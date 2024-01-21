from drivers.utils import load_clvm_hex, raw_hash
from drivers.portal import get_message_coin_puzzle_1st_curry
from chia.types.blockchain_format.program import Program
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.wallet.cat_wallet.cat_wallet import CAT_MOD_HASH

WRAPPED_TAIL_MOD = load_clvm_hex("puzzles/wrapped_tail.clvm.hex")
WRAPPED_TAIL_MOD_HASH = WRAPPED_TAIL_MOD.get_tree_hash()

CAT_MINTER_MOD = load_clvm_hex("puzzles/cat_minter.clvm.hex")

CAT_MINT_AND_PAYOUT_MOD = load_clvm_hex("puzzles/cat_mint_and_payout.clvm.hex")
CAT_MINT_AND_PAYOUT_MOD_HASH = CAT_MINT_AND_PAYOUT_MOD.get_tree_hash()

CAT_BURNER_MOD = load_clvm_hex("puzzles/cat_burner.clvm.hex")

BURN_INNER_PUZZLE_MOD = load_clvm_hex("puzzles/burn_inner_puzzle.clvm.hex")
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
