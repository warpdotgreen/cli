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
from blspy import PrivateKey, AugSchemeMPL, G1Element, G2Element
from chia.types.spend_bundle import SpendBundle
from chia.types.blockchain_format.program import INFINITE_COST
from typing import Tuple
from drivers.multisig import *
import json
import qrcode

@click.group()
def rekey():
    pass


@rekey.command()
def sign_tx():
    pass

@rekey.command()
@click.option('--sigs', required=True, help='Signatures, separated by commas')
@async_func
@with_node
async def broadcast_spend():
    pass
