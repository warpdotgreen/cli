import click
import json
from web3 import Web3
from commands.config import get_config_item
import hashlib

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
def get_eth_deployment_data(weth_address):
    click.echo("Constructing txes based on config...")

    w3 = Web3(Web3.HTTPProvider(get_config_item(["ethereum", "rpc_url"])))

    portal_artifact = json.loads(
        open('artifacts/contracts/Portal.sol/Portal.json', 'r').read()
      )
    eth_token_bridge_artifact = json.loads(
        open('artifacts/contracts/EthTokenBridge.sol/EthTokenBridge.json', 'r').read()
      )
    
    portal_safe_address = get_config_item(["ethereum", "portal_safe_address"])
    deployer_safe_address = get_config_item(["ethereum", "deployer_safe_address"])
    create_call_address = get_config_item(["ethereum", "create_call_address"])

    salt = hashlib.sha256(b"yakuhito6").digest()

    portal_constructor_data = w3.eth.contract(
        abi=portal_artifact['abi'],
        bytecode=portal_artifact['bytecode']
    ).constructor().build_transaction()['data']
    open("portal_constructor.data", "w").write(portal_constructor_data)

    portal_address = predict_create2_address(create_call_address, salt, portal_constructor_data)

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
    print(f"\t Predicted address: {portal_address}")

    print("Tx 2: deploy EthTokenBridge")
    print(f"\t To: {create_call_address}")
    print(f"\t Contract method selector: performCreate2")
    print(f"\t Value: 0")
    print(f"\t Data: see eth_token_bridge_constructor.data")
    print(f"\t Salt: 0x{salt.hex()}")
    print(f"\t Predicted address: {predict_create2_address(create_call_address, salt, eth_token_bridge_constructor_data)}")

    print("Tx 3: deploy TransparentUpgradeableProxy")

@deployment.command()
def launch_xch_multisig():
    pass
