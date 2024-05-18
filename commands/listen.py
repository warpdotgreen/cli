import click
from commands.models import *
import time
import logging
from commands.followers.eth_follower import EthereumFollower
from commands.followers.xch_follower import ChiaFollower
import asyncio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@click.option('--log-startup-connection-errors', is_flag=True, default=False)
@click.command()
def listen(log_startup_connection_errors: bool):
    eth_follower = EthereumFollower("eth", False)
    bse_follower = EthereumFollower("bse", True)
    xch_follower = ChiaFollower("xch")

    asyncio.run(xch_follower.wait_for_node(log_startup_connection_errors))
    asyncio.run(eth_follower.wait_for_node(log_startup_connection_errors))
    asyncio.run(bse_follower.wait_for_node(log_startup_connection_errors))

    loop = asyncio.new_event_loop()
    xch_follower.run(loop)
    eth_follower.run(loop)
    bse_follower.run(loop)
    loop.run_forever()
    
    while True:
        time.sleep(5)
