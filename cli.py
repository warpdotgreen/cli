import click

@click.group()
def cli():
    pass

@click.group()
def keys():
    pass

@keys.command()
def generate_xch_key():
    click.echo("Generating XCH key...")

@keys.command()
def generate_eth_key():
    click.echo("Generating ETH key...")

cli.add_command(keys)

if __name__ == '__main__':
    cli()
