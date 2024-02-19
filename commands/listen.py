import click
from commands.cli_wrappers import async_func
from commands.models import *
from commands.config import get_config_item

async def eth_listener():
    pass

@click.command()
@async_func
async def listen():
    await eth_listener()
    db = setup_database()
