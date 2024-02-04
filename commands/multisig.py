import click
from commands.cli_wrappers import *

@click.group()
def multisig():
    pass

@multisig.command()
@async_func
@with_node
async def start_new_spend():
    pass
