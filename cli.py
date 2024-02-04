import click
import secrets
from chia.util.keychain import bytes_to_mnemonic, mnemonic_to_seed
from chia.wallet.derive_keys import _derive_path
from blspy import AugSchemeMPL

@click.group()
def cli():
    pass

@click.group()
def keys():
    pass

@keys.command()
def generate_xch_key():
    click.echo("Generating XCH key...")
    entropy = secrets.token_bytes(16)
    mnemonic = bytes_to_mnemonic(entropy)
    click.echo(f"Mnemonic: {mnemonic}")
    seed = mnemonic_to_seed(mnemonic)
    root_key = AugSchemeMPL.key_gen(seed)
    private_key = _derive_path(root_key, [12381, 8444, 7, 0])
    public_key = private_key.get_g1()
    click.echo(f"Private Key: {bytes(private_key).hex()}")
    click.echo(f"Public Key: {public_key}")

@keys.command()
def generate_eth_key():
    click.echo("Generating ETH key...")

cli.add_command(keys)

if __name__ == '__main__':
    cli()
