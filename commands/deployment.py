# note from yak: this file is ungly, but it works.
import click
import json
from web3 import Web3
from commands.config import get_config_item
from chia.wallet.trading.offer import Offer, OFFER_MOD
from chia.types.blockchain_format.program import Program
from chia.types.spend_bundle import SpendBundle
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.types.condition_opcodes import ConditionOpcode
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_LAUNCHER_HASH, SINGLETON_LAUNCHER, launch_conditions_and_coinsol
from chia.wallet.puzzles.singleton_top_layer_v1_1 import pay_to_singleton_puzzle
from chia.util.keychain import bytes_to_mnemonic, mnemonic_to_seed
from commands.keys import mnemonic_to_validator_pk
from chia.types.blockchain_format.coin import Coin
from chia.types.coin_spend import CoinSpend
from drivers.multisig import get_multisig_inner_puzzle
from drivers.portal import *
from drivers.wrapped_assets import get_cat_minter_puzzle, get_cat_burner_puzzle, get_wrapped_tail
from drivers.wrapped_cats import get_locker_puzzle, get_unlocker_puzzle
from chia.wallet.puzzles.p2_delegated_conditions import puzzle_for_pk, solution_for_conditions
from commands.config import get_config_item
from chia_rs import G1Element, AugSchemeMPL
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from typing import Tuple
import hashlib
import secrets
import json
from commands.cli_wrappers import async_func

DEPLOYMENT_SALT = hashlib.sha256(b"you cannot imagine how many times yak manually changed this string during testing").digest()

def print_spend_instructions(
    sb: SpendBundle,
    spent_coin_id: bytes
):
    open("sb.json", "w").write(json.dumps(sb.to_json_dict(), indent=4))
    open("push_request.json", "w").write(json.dumps({"spend_bundle": sb.to_json_dict()}, indent=4))

    click.echo("SpendBundle created and saved to sb.json")
    click.echo("To spend: chia rpc full_node push_tx -j push_request.json")
    click.echo("To follow in mempool: chia rpc full_node get_mempool_items_by_coin_name '{\"coin_name\": \"" + spent_coin_id.hex() + "\"}'")
    click.echo("To confirm: chia rpc full_node get_coin_record_by_name '{\"name\": \"" + spent_coin_id.hex() + "\"}'")


@click.group()
def deployment():
    pass


def predict_create2_address(sender, init_code):
    sender_address_bytes = Web3.to_bytes(hexstr=sender)
    init_code_bytes = Web3.to_bytes(hexstr=init_code)
    
    create2_prefix = b'\xff'
    create2_inputs = create2_prefix + sender_address_bytes + DEPLOYMENT_SALT + Web3.keccak(init_code_bytes)
    create2_hash = Web3.keccak(create2_inputs)
    
    contract_address_bytes = create2_hash[12:]
    
    contract_address = Web3.to_checksum_address(contract_address_bytes)
    return contract_address


@deployment.command()
@click.option('--weth-address', required=True, help="WETH contract address to be used by the bridge (set to 'meth' to also deploy mETH contract)")
@click.option('--tip', required=True, help="Tip, in parts out of 10000 (e.g., 30 means 0.3%)")
@click.option('--chain', required=True, help="Network id where you want to deploy (e.g., eth/bse)")
def get_evm_deployment_data(weth_address: str, tip: int, chain: str):
    deploy_meth = weth_address == "meth" 
    tip = int(tip)

    if deploy_meth:
        weth_address = None
        click.echo("Will also deploy mETH contract! :)")

    click.echo("Constructing txes based on config...")
    wei_per_message_toll = get_config_item([chain, "wei_per_message_toll"])

    w3 = Web3(Web3.HTTPProvider(get_config_item([chain, "rpc_url"])))

    millieth_artifact = json.loads(
        open('artifacts/contracts/MilliETH.sol/MilliETH.json', 'r').read()
      )
    portal_artifact = json.loads(
        open('artifacts/contracts/Portal.sol/Portal.json', 'r').read()
      )
    erc20_bridge_artiface = json.loads(
        open('artifacts/contracts/ERC20Bridge.sol/ERC20Bridge.json', 'r').read()
      )
    proxy_artifact = json.loads(
        open('artifacts/@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol/TransparentUpgradeableProxy.json', 'r').read()
      )
    
    deployer_safe_address = get_config_item([chain, "deployer_safe_address"])
    create_call_address = get_config_item([chain, "create_call_address"])

    meth_contract = w3.eth.contract(
        abi=millieth_artifact['abi'],
        bytecode=millieth_artifact['bytecode']
    )
    meth_constructor_data = meth_contract.constructor().build_transaction()['data']
    open("millieth.data", "w").write(meth_constructor_data)

    weth_address = predict_create2_address(create_call_address, meth_constructor_data)

    portal_contract = w3.eth.contract(
        abi=portal_artifact['abi'],
        bytecode=portal_artifact['bytecode']
    )
    portal_constructor_data = portal_contract.constructor().build_transaction()['data']
    open("portal_constructor.data", "w").write(portal_constructor_data)

    portal_logic_address = predict_create2_address(create_call_address, portal_constructor_data)

    portal_initialization_data = portal_contract.encodeABI(
        fn_name='initialize',
        args=[
            Web3.to_bytes(hexstr=deployer_safe_address),
            wei_per_message_toll,
            [Web3.to_bytes(hexstr=addr) for addr in get_config_item([chain, "hot_addresses"])],
            get_config_item([chain, "portal_threshold"]),
            [Web3.to_bytes(hexstr="0x" + b"xch".hex())]
        ]
    )
    open("portal_initialization.data", "w").write(portal_initialization_data)
    
    proxy_constructor_data = w3.eth.contract(
        abi=proxy_artifact['abi'],
        bytecode=proxy_artifact['bytecode']
    ).constructor(
        Web3.to_bytes(hexstr=portal_logic_address),
        Web3.to_bytes(hexstr=deployer_safe_address),
        portal_initialization_data
    ).build_transaction({
        'gas': 5000000000
    })['data']
    open("proxy_constructor.data", "w").write(proxy_constructor_data)

    portal_address = predict_create2_address(create_call_address, proxy_constructor_data)

    eth_token_bridge_constructor_data = w3.eth.contract(
        abi=erc20_bridge_artiface['abi'],
        bytecode=erc20_bridge_artiface['bytecode']
    ).constructor(
        tip,
        Web3.to_bytes(hexstr=portal_address),
        Web3.to_bytes(hexstr=weth_address),
        10 ** 12 if deploy_meth else 1,
        Web3.to_bytes(hexstr="0x" + b"xch".hex())
    ).build_transaction({
        'gas': 5000000000
    })['data']
    open("eth_token_bridge_constructor.data", "w").write(eth_token_bridge_constructor_data)

    click.echo("")
    click.echo("")
    click.echo("Deployment batch")
    click.echo("-------------------")

    if deploy_meth:
        click.echo("Tx 0: deploy MilliETH")
        click.echo(f"\t To: {create_call_address}")
        click.echo(f"\t Contract method selector: performCreate2")
        click.echo(f"\t Value: 0")
        click.echo(f"\t Data: see millieth.data")
        click.echo(f"\t Salt: 0x{DEPLOYMENT_SALT.hex()}")
        click.echo(f"\t Predicted address: {weth_address}")

    click.echo("Tx 1: deploy Portal")
    click.echo(f"\t To: {create_call_address}")
    click.echo(f"\t Contract method selector: performCreate2")
    click.echo(f"\t Value: 0")
    click.echo(f"\t Data: see portal_constructor.data")
    click.echo(f"\t Salt: 0x{DEPLOYMENT_SALT.hex()}")
    click.echo(f"\t Predicted address: {portal_logic_address}")

    click.echo("Tx 2: deploy TransparentUpgradeableProxy")
    click.echo(f"\t To: {create_call_address}")
    click.echo(f"\t Contract method selector: performCreate2")
    click.echo(f"\t Value: 0")
    click.echo(f"\t Data: see proxy_constructor.data")
    click.echo(f"\t Salt: 0x{DEPLOYMENT_SALT.hex()}")
    click.echo(f"\t Predicted address: {portal_address}")

    bridge_address = predict_create2_address(create_call_address, eth_token_bridge_constructor_data)
    click.echo("Tx 3: deploy ERC20Bridge")
    click.echo(f"\t To: {create_call_address}")
    click.echo(f"\t Contract method selector: performCreate2")
    click.echo(f"\t Value: 0")
    click.echo(f"\t Data: see eth_token_bridge_constructor.data")
    click.echo(f"\t Salt: 0x{DEPLOYMENT_SALT.hex()}")
    click.echo(f"\t Predicted address: {bridge_address}")

    if len(get_config_item(["xch", "portal_launcher_id"])) != 64:
        click.echo("")
        click.echo("Warning: xch.portal_launcher_id is not set. You should launch the portal, and only then use this function. This will ensure 'initializePuzzleHashes' is called in the same transaction.")
        return
    
    click.echo("Tx 2: call initializePuzzleHashes")
    click.echo(f"\t To: {bridge_address}")
    click.echo(f"\t Contract ABI: take from artifacts/contracts/ERC20Bridge.sol/ERC20Bridge.json")
    click.echo(f"\t Contract method selector: initializePuzzleHashes")
    click.echo(f"\t Data: see below")
    _get_xch_info(chain, bridge_address)


async def securely_launch_singleton(
    offer: Offer,
    get_target_singleton_inner_puzze: any,
    comments: List[Tuple[str, str]] = []
) -> Tuple[bytes32, SpendBundle]: # launcher_id, spend_bundle
    offer_sb: SpendBundle = offer.to_spend_bundle()
    coin_spends = []
    for cs in offer_sb.coin_spends:
        if cs.coin.parent_coin_info != b'\x00' * 32:
            coin_spends.append(cs)

    # create launcher parent parent coin
    # this coin makes it impossible for the singleton to have the predicted launcher id
    # unless it has exactly the intended ph
    entropy = secrets.token_bytes(16)
    mnemonic = bytes_to_mnemonic(entropy)
    temp_private_key = mnemonic_to_validator_pk(mnemonic)
    temp_public_key = temp_private_key.get_g1()
            
    launcher_parent_puzzle = puzzle_for_pk(Program.to(temp_public_key))
    launcher_parent_puzzle_hash = launcher_parent_puzzle.get_tree_hash()

    nonce = secrets.token_bytes(32)
    launcher_parent_parent = offer.get_offered_coins()[None][0]
    launcher_parent_parent_puzzle = OFFER_MOD
    launcher_parent_parent_solution = Program.to([
        [nonce, [launcher_parent_puzzle_hash, 1]]
    ])
    launcher_parent_parent_spend = CoinSpend(launcher_parent_parent, launcher_parent_parent_puzzle, launcher_parent_parent_solution)
    coin_spends.append(launcher_parent_parent_spend)

    # spend launcher coin
    launcher_parent = Coin(
        launcher_parent_parent.name(),
        launcher_parent_puzzle_hash,
        1
    )
    launcher_coin = Coin(
        launcher_parent.name(),
        SINGLETON_LAUNCHER_HASH,
        1
    )

    launcher_id = launcher_coin.name()
    click.echo(f"Launcher coin id: {launcher_id.hex()}")

    conditions, launcher_spend = launch_conditions_and_coinsol(
        launcher_parent,
        get_target_singleton_inner_puzze(launcher_id),
        comments,
        1
    )
    coin_spends.append(launcher_spend)

    # finally, spend launcher parent
    launcher_parent_solution = solution_for_conditions(Program.to(conditions))
    launcher_parent_spend = CoinSpend(launcher_parent, launcher_parent_puzzle, launcher_parent_solution)
    coin_spends.append(launcher_parent_spend)

    def just_return_the_fing_key(arg: any):
        return temp_private_key

    sb_just_for_sig: SpendBundle = await sign_coin_spends(
        [launcher_parent_spend],
        just_return_the_fing_key,
        just_return_the_fing_key,
        bytes.fromhex(get_config_item(["xch", "agg_sig_data"])),
        DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM,
        []
    )
    sig = sb_just_for_sig.aggregated_signature
            
    sb = SpendBundle(
        coin_spends,
        AugSchemeMPL.aggregate([offer_sb.aggregated_signature, sig])
    )
    print_spend_instructions(sb, launcher_coin.name())

    return [launcher_id, sb]


# chia rpc wallet create_offer_for_ids '{"offer":{"1":-1},"fee":4200000000,"driver_dict":{},"validate_only":false}'
@deployment.command()
@click.option('--offer', default="help", help='Offer to build the portal from (must offer exactly 1 mojo + include min network fee)')
@async_func
async def launch_xch_portal(offer):
    if offer == "help":
        click.echo("Oops, you forgot --offer!")
        click.echo('chia rpc wallet create_offer_for_ids \'{"offer":{"1":-1},"fee":4200000000,"driver_dict":{},"validate_only":false}\'')
        return
    offer = Offer.from_bech32(offer)
    
    portal_threshold = get_config_item(["xch", "portal_threshold"])
    portal_pks = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "portal_keys"])]
    multisig_threshold = get_config_item(["xch", "multisig_threshold"])
    multisig_pks = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "multisig_keys"])]

    def get_portal_receiver_inner_puzzle_pls(launcher_id: bytes32):
        return get_portal_receiver_inner_puzzle(
            launcher_id,
            portal_threshold,
            portal_pks,
            get_multisig_inner_puzzle(multisig_pks, multisig_threshold).get_tree_hash()
        )

    await securely_launch_singleton(
        offer,
        get_portal_receiver_inner_puzzle_pls,
        [("the", "portal")]
    )


@deployment.command()
@click.option('--other-chain', required=True, help='Other blockchain config entry key (e.g., eth/bse)')
def get_xch_info(other_chain: str):
    _get_xch_info(other_chain, get_config_item([other_chain, "erc20_bridge_address"]))


def _get_xch_info(other_chain: str, erc_20_bridge_address: str):
    portal_launcher_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))
    portal_threshold = get_config_item(["xch", "portal_threshold"])

    minter_puzzle = get_cat_minter_puzzle(
        portal_launcher_id,
        other_chain.encode(),
        bytes.fromhex(erc_20_bridge_address.replace("0x", ""))
    )

    burner_puzzle = get_cat_burner_puzzle(
        other_chain.encode(),
        bytes.fromhex(erc_20_bridge_address.replace("0x", ""))
    )

    click.echo(f"Portal launcher id: {portal_launcher_id.hex()}")
    click.echo(f"Portal signature threshold: {portal_threshold}")
    click.echo(f"Burner puzzle hash: {burner_puzzle.get_tree_hash().hex()}")
    click.echo(f"Minter puzzle hash: {minter_puzzle.get_tree_hash().hex()}")


@deployment.command()
@click.option('--chain', required=True, help='Other blockchain config entry key (eth/bse)')
@click.option('--address', required=True, help='ERC20 contract address')
def get_wrapped_erc20_asset_id(chain: str, address: str):
    portal_launcher_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))
    erc20_bridge_address = bytes.fromhex(get_config_item([chain, "erc20_bridge_address"]).replace("0x", ""))

    address: bytes = bytes.fromhex(address.replace("0x", ""))
    tail = get_wrapped_tail(
        portal_launcher_id,
        chain.encode(),
        erc20_bridge_address,
        address
    )
    print(f"Tail hash: {tail.get_tree_hash().hex()}")


@deployment.command()
@click.option('--asset-id', required=True, help="CAT asset id (tail hash) - use 'xch' for XCH")
@click.option('--tip', required=True, help="Tip, in parts out of 10000 (e.g., 30 means 0.3%)")
@click.option('--chain', required=True, help="Id of network where you want to deploy (e.g., eth/bse)")
@click.option('--erc20-name', required=True, help="Name of the new ERC-20 asset")
@click.option('--erc20-symbol', required=True, help="Symbol of the new ERC-20 asset")
def get_wrapped_cat_deployment_data(
    asset_id: str,
    tip: int,
    chain: str,
    erc20_name: str,
    erc20_symbol: str
):
    if asset_id == 'xch':
        asset_id = "00" * 32
    if len(asset_id) != 64:
        click.echo("Asset id must be 32 bytes long")
        return

    asset_id: bytes32 = bytes.fromhex(asset_id)
    tip = int(tip)

    cat_decimals = 1000
    if asset_id == b"\x00" * 32:
        cat_decimals = 10 ** 12
    mojoToTokenRatio = 10 ** 18 // cat_decimals

    click.echo(f"Mojo to token ratio: {mojoToTokenRatio}")

    click.echo("Constructing txes based on config...")
    w3 = Web3(Web3.HTTPProvider(get_config_item([chain, "rpc_url"])))

    wrapped_cat_artifact = json.loads(
        open('artifacts/contracts/WrappedCAT.sol/WrappedCAT.json', 'r').read()
      )
    
    create_call_address = get_config_item([chain, "create_call_address"])

    portal_address = get_config_item([chain, "portal_address"])

    wrapped_cat_constructor_data = w3.eth.contract(
        abi=wrapped_cat_artifact['abi'],
        bytecode=wrapped_cat_artifact['bytecode']
    ).constructor(
        erc20_name,
        erc20_symbol,
        Web3.to_bytes(hexstr=portal_address),
        tip,
        mojoToTokenRatio,
        Web3.to_bytes(hexstr="0x" + b"xch".hex()) # other chain
    ).build_transaction({
        'gas': 5000000000
    })['data']
    data_filename = f"wrapped_cat.{asset_id[:3].hex()}.data"
    open(data_filename, "w").write(wrapped_cat_constructor_data)

    wrapped_cat_address = predict_create2_address(create_call_address, wrapped_cat_constructor_data)

    print(f'npx hardhat verify {wrapped_cat_address} \'{erc20_name}\' \'{erc20_symbol}\' {portal_address} {tip} {mojoToTokenRatio} {"0x" + b"xch".hex()}')

    click.echo("")
    click.echo("")
    click.echo("Deployment batch")
    click.echo("-------------------")

    click.echo("Tx 1: deploy WrappedCAT")
    click.echo(f"\t To: {create_call_address}")
    click.echo(f"\t Contract method selector: performCreate2")
    click.echo(f"\t Value: 0")
    click.echo(f"\t Data: see {data_filename}")
    click.echo(f"\t Salt: 0x{DEPLOYMENT_SALT.hex()}")
    click.echo(f"\t Predicted address: {wrapped_cat_address}")
    
    click.echo("Tx 2: call initializePuzzleHashes")
    click.echo(f"\t To: {wrapped_cat_address}")
    click.echo(f"\t Contract ABI: take from artifacts/contracts/WrappedCAT.sol/WrappedCAT.json")
    click.echo(f"\t Contract method selector: initializePuzzleHashes")
    click.echo(f"\t Data: see below")
    _get_wrapped_cat_info(chain, asset_id, bytes.fromhex(wrapped_cat_address.replace("0x", "")))


@deployment.command()
@click.option('--chain', required=True, help='EVM blockchain config entry key (e.g., eth/bse)')
@click.option('--asset-id', required=True, help="CAT asset id (tail hash) - use 'xch' for XCH")
@click.option('--contract', required=True, help="Wrapped CAT contract address")
def get_wrapped_cat_info(chain: str, asset_id: str, contract: str):
    if asset_id == 'xch':
        asset_id = "00" * 32
    if len(asset_id) != 64:
        click.echo("Asset id must be 32 bytes long")
        return
    
    _get_wrapped_cat_info(
        chain,
        bytes.fromhex(asset_id),
        bytes.fromhex(contract.replace("0x", ""))
    )


def _get_wrapped_cat_info(evm_chain: str, asset_id: bytes32, contract_address: bytes):
    portal_launcher_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))
    if asset_id == b"\x00" * 32:
        asset_id = None

    locker_puzzle = get_locker_puzzle(
        evm_chain.encode(),
        contract_address,
        portal_launcher_id,
        asset_id
    )
    unlocker_puzzle = get_unlocker_puzzle(
        evm_chain.encode(),
        contract_address,
        portal_launcher_id,
        asset_id
    )

    click.echo(f"Portal launcher id: {portal_launcher_id.hex()}")
    click.echo(f"Asset id: {'None' if asset_id is None else asset_id.hex()}")
    click.echo(f"Contract address (no checksum): {contract_address.hex()}")
    click.echo(f"Locker puzzle hash: {locker_puzzle.get_tree_hash().hex()}")
    click.echo(f"Unlocker puzzle hash: {unlocker_puzzle.get_tree_hash().hex()}")
