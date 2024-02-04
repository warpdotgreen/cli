# note from yak: this file is ungly, but it works.
import click
import json
from web3 import Web3
from commands.config import get_config_item
from chia.wallet.trading.offer import Offer, OFFER_MOD
from chia.types.blockchain_format.program import Program
from chia.types.spend_bundle import SpendBundle
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_LAUNCHER_HASH, SINGLETON_LAUNCHER, launch_conditions_and_coinsol
from chia.wallet.puzzles.singleton_top_layer_v1_1 import pay_to_singleton_puzzle
from chia.types.blockchain_format.coin import Coin
from chia.types.coin_spend import CoinSpend
from drivers.multisig import get_multisig_inner_puzzle
from commands.config import get_config_item
from blspy import G1Element
import hashlib
import json

@click.group()
def deployment():
    pass

def predict_create2_address(sender, salt, init_code):
    sender_address_bytes = Web3.to_bytes(hexstr=sender)
    salt_bytes = Web3.to_bytes(salt) if isinstance(salt, str) else salt
    init_code_bytes = Web3.to_bytes(hexstr=init_code)
    
    create2_prefix = b'\xff'
    create2_inputs = create2_prefix + sender_address_bytes + salt_bytes + Web3.keccak(init_code_bytes)
    create2_hash = Web3.keccak(create2_inputs)
    
    contract_address_bytes = create2_hash[12:]
    
    contract_address = Web3.to_checksum_address(contract_address_bytes)
    return contract_address

@deployment.command()
@click.option('--weth-address', required=True, help='WETH contract address to be used by the bridge')
@click.option('--wei-per-message-fee', default=10 ** (18 - 3 - 2), help='Fee to send a message from ETH (default: 1 cent @ $1000 ETH)')
def get_eth_deployment_data(weth_address, wei_per_message_fee):
    click.echo("Constructing txes based on config...")

    w3 = Web3(Web3.HTTPProvider(get_config_item(["ethereum", "rpc_url"])))

    portal_artifact = json.loads(
        open('artifacts/contracts/Portal.sol/Portal.json', 'r').read()
      )
    eth_token_bridge_artifact = json.loads(
        open('artifacts/contracts/EthTokenBridge.sol/EthTokenBridge.json', 'r').read()
      )
    proxy_artifact = json.loads(
        open('artifacts/@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol/TransparentUpgradeableProxy.json', 'r').read()
      )
    
    portal_safe_address = get_config_item(["ethereum", "portal_safe_address"])
    deployer_safe_address = get_config_item(["ethereum", "deployer_safe_address"])
    create_call_address = get_config_item(["ethereum", "create_call_address"])

    salt = hashlib.sha256(b"yakuhito").digest()

    portal_contract = w3.eth.contract(
        abi=portal_artifact['abi'],
        bytecode=portal_artifact['bytecode']
    )
    portal_constructor_data = portal_contract.constructor().build_transaction()['data']
    open("portal_constructor.data", "w").write(portal_constructor_data)

    portal_logic_address = predict_create2_address(create_call_address, salt, portal_constructor_data)

    portal_initialization_data = portal_initialization_data = portal_contract.encodeABI(
        fn_name='initialize',
        args=[
            Web3.to_bytes(hexstr=portal_safe_address),
            Web3.to_bytes(hexstr=deployer_safe_address),
            wei_per_message_fee
        ]
    )
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

    portal_address = predict_create2_address(create_call_address, salt, proxy_constructor_data)

    eth_token_bridge_constructor_data = w3.eth.contract(
        abi=eth_token_bridge_artifact['abi'],
        bytecode=eth_token_bridge_artifact['bytecode']
    ).constructor(
        Web3.to_bytes(hexstr=portal_address),
        Web3.to_bytes(hexstr=deployer_safe_address),
        Web3.to_bytes(hexstr=weth_address)
    ).build_transaction({
        'gas': 5000000000
    })['data']
    open("eth_token_bridge_constructor.data", "w").write(eth_token_bridge_constructor_data)

    print("")
    print("")
    print("Deployment batch #1")
    print("-------------------")
    print("Tx 1: deploy Portal")
    print(f"\t To: {create_call_address}")
    print(f"\t Contract method selector: performCreate2")
    print(f"\t Value: 0")
    print(f"\t Data: see portal_constructor.data")
    print(f"\t Salt: 0x{salt.hex()}")
    print(f"\t Predicted address: {portal_logic_address}")

    print("Tx 2: deploy TransparentUpgradeableProxy")
    print(f"\t To: {create_call_address}")
    print(f"\t Contract method selector: performCreate2")
    print(f"\t Value: 0")
    print(f"\t Data: see proxy_constructor.data")
    print(f"\t Salt: 0x{salt.hex()}")
    print(f"\t Predicted address: {portal_address}")

    print("Tx 3: deploy EthTokenBridge")
    print(f"\t To: {create_call_address}")
    print(f"\t Contract method selector: performCreate2")
    print(f"\t Value: 0")
    print(f"\t Data: see eth_token_bridge_constructor.data")
    print(f"\t Salt: 0x{salt.hex()}")
    print(f"\t Predicted address: {predict_create2_address(create_call_address, salt, eth_token_bridge_constructor_data)}")


# chia rpc wallet create_offer_for_ids '{"offer":{"1":-1},"fee":4200000000,"driver_dict":{},"validate_only":false}'
@deployment.command()
@click.option('--offer', default="help", help='Offer to build a multisig from (must offer  exactly 1 mojo + include min network fee)')
def launch_xch_multisig(offer):
    if offer == "help":
        click.echo("Oops, you forgot --offer!")
        click.echo('chia rpc wallet create_offer_for_ids \'{"offer":{"1":-1},"fee":4200000000,"driver_dict":{},"validate_only":false}\'')
        return
    offer = Offer.from_bech32(offer)
    offer_sb: SpendBundle = offer.to_spend_bundle()
    coin_spends = []
    for cs in offer_sb.coin_spends:
        if cs.coin.parent_coin_info != b'\x00' * 32:
            coin_spends.append(cs)

    # create launcher coin
    nonce = b"multisig" * 4
    launcher_parent = offer.get_offered_coins()[None][0]
    launcher_parent_puzzle = OFFER_MOD
    launcher_parent_solution = Program.to([
        [nonce, [SINGLETON_LAUNCHER_HASH, 1]]
    ])
    launcher_parent_spend = CoinSpend(launcher_parent, launcher_parent_puzzle, launcher_parent_solution)
    coin_spends.append(launcher_parent_spend)
            
    # spend launcher coin
    launcher_coin = Coin(
        launcher_parent.name(),
        SINGLETON_LAUNCHER_HASH,
        1
    )

    launcher_id = launcher_coin.name()
    click.echo(f"Multisig launcher coin id: {launcher_id.hex()}")
    p2_puzzle_hash = pay_to_singleton_puzzle(launcher_id).get_tree_hash()
    click.echo(f"Multisig p2_singleton ph: {p2_puzzle_hash.hex()}")

    threshold = get_config_item(["chia", "multisig_treshold"])
    pks = get_config_item(["chia", "multisig_keys"])
    pks = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in pks]
    multisig_inner_puzzle = get_multisig_inner_puzzle(pks, threshold)

    _, launcher_spend = launch_conditions_and_coinsol(
        launcher_parent,
        multisig_inner_puzzle,
        [("yep", "multisig")],
        1
    )
    coin_spends.append(launcher_spend)
            
    sb: SpendBundle = SpendBundle(coin_spends, offer_sb.aggregated_signature)
    open("sb.json", "w").write(json.dumps(sb.to_json_dict(), indent=4))
    open("push_request.json", "w").write(json.dumps({"spend_bundle": sb.to_json_dict()}, indent=4))

    click.echo("SpendBundle created and saved to sb.json")
    click.echo("To spend: chia rpc full_node push_tx -j push_request.json")
