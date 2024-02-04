import click
import json
from web3 import Web3
from commands.config import get_config_item

@click.group()
def deployment():
    pass

def load_contract_artifact(file_name):
    with open(f'artifacts/contracts/{file_name}/{file_name.split(".")[0]}.json') as f:
        return json.load(f)

@deployment.command()
def get_eth_deployment_data():
    click.echo("Constructing txes based on config...")

    rpc_url = get_config_item(["ethereum", "rpc_url"])
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    portal_artifact = load_contract_artifact('Portal.sol')
    eth_token_bridge_artifact = load_contract_artifact('EthTokenBridge.sol')

    print("PORTAL DEPLOYMENT TX:")
    portal_constructor_data = w3.eth.contract(
        abi=portal_artifact['abi'],
        bytecode=portal_artifact['bytecode']
    ).constructor().build_transaction()['data']

    # TODO

    print(portal_constructor_data)

    # eth_token_bridge_constructor_args = w3.eth.contract(
    #     abi=eth_token_bridge_artifact['abi'],
    #     bytecode=eth_token_bridge_artifact['bytecode']
    # ).constructor(arg1, arg2).build_transaction()['data']

@deployment.command()
def launch_xch_multisig():
    pass
