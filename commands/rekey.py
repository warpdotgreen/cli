import click
from commands.cli_wrappers import *
from typing import List
from blspy import G1Element
from drivers.multisig import *
from drivers.portal import get_portal_rekey_delegated_puzzle
from commands.multisig import get_cold_key_signature

@click.group()
def rekey():
    pass


@rekey.command()
@click.option('--new-message-keys', required=True, help='New set of hot keys, separated by commas')
@click.option('--new-message-threshold', required=True, help='New threshold required for messages')
@click.option('--new-update-keys', required=True, help='New set of cold keys, separated by commas')
@click.option('--new-update-threshold', required=True, help='New threshold required for updates')
@click.option('--validator-index', required=True, help='Your validator index')
@click.option('--use-debug-method', is_flag=True, default=False, help='Use debug signing method')
def sign_tx(
    new_message_keys: str,
    new_message_threshold: int,
    new_update_keys: str,
    new_update_threshold: int,
    validator_index: int,
    use_debug_method: bool
):
    new_message_keys: List[G1Element] = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_message_keys.split(',')]
    new_update_keys: List[G1Element] = [G1Element.from_bytes(bytes.fromhex(key)) for key in new_update_keys.split(',')]
    new_message_threshold = int(new_message_threshold)
    new_update_threshold = int(new_update_threshold)

    portal_launcher_id = bytes.fromhex(get_config_item(["xch", "portal_launcher_id"]))

    current_message_threshold = int(get_config_item(["xch", "portal_threshold"]))
    current_message_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "portal_keys"]).split(',')]
    current_update_threshold = int(get_config_item(["xch", "multisig_threshold"]))
    current_update_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in get_config_item(["xch", "multisig_keys"]).split(',')]

    updater_delegated_puzzle = get_portal_rekey_delegated_puzzle(
        portal_launcher_id,
        current_message_threshold,
        current_message_keys,
        new_message_threshold,
        new_message_keys,
        current_update_threshold,
        current_update_keys,
        new_update_threshold,
        new_update_keys
    )
    message_to_sign = updater_delegated_puzzle.get_tree_hash()

    get_cold_key_signature(message_to_sign, validator_index, use_debug_method)


@rekey.command()
@click.option('--sigs', required=True, help='Signatures, separated by commas')
@click.option('--new-keys', required=True, help='New set of cold keys, separated by commas')
@click.option('--new-threshold', required=True, help='New threshold required for updates')
@async_func
@with_node
async def broadcast_spend():
    pass
