import click
from commands.cli_wrappers import async_func
from commands.models import *
from commands.config import get_config_item
from web3 import Web3
import time
import logging
import json
from commands.followers.eth_follower import EthereumFollower
from commands.followers.xch_follower import ChiaFollower

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@click.command()
@async_func
async def listen():
    follower = ChiaFollower("xch")

    await follower.run()
