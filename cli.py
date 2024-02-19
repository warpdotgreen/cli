import click
from commands.keys import keys
from commands.deployment import deployment
from commands.multisig import multisig
from commands.listen import listen

@click.group()
def cli():
    pass


cli.add_command(keys)
cli.add_command(deployment)
cli.add_command(multisig)
cli.add_command(listen)

if __name__ == '__main__':
    cli()
