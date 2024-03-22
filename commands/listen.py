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
import asyncio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@click.command()
@async_func
async def listen():
    eth_follower = EthereumFollower("eth")
    xch_follower = ChiaFollower("xch")

    catch_up = True
    while catch_up:
        catch_up = await eth_follower.catchUp()
        catch_up = (await xch_follower.catchUp()) or catch_up

    loop = asyncio.get_event_loop()
    xch_follower.run(loop)
    eth_follower.run(loop)
    # loop.run_forever()
    
    while True:
        await asyncio.sleep(5)
