import click
from commands.cli_wrappers import async_func
from commands.models import *
from commands.config import get_config_item
from web3 import Web3
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def revertBlock(db, chain_id: bytes, height: int):
    logging.info(f"Block #{chain_id.decode()}-{height} reverted.")
    db.query(Block).filter(Block.height == height and Block.chain_id == chain_id).delete()
    db.commit()


def addBlock(db, chain_id: bytes, height: int, hash: bytes, prev_hash: bytes):
    logging.info(f"Block #{chain_id.decode()}-{height} added to db.")
    db.add(Block(height=height, hash=hash, chain_id=chain_id, prev_hash=prev_hash))


# returns new block height we should sync to
def syncBlockUsingHeight(db, web3: Web3, chain_id: bytes, height: int, block=None) -> int: 
    if block is None:
      block = web3.eth.get_block(height)
    logging.info(f"Processing block #{chain_id.decode()}-{height} with hash {block['hash'].hex()}...")

    block_height = block['number']
    assert block_height == height

    block_hash = bytes(block['hash'])
    block_prev_hash = bytes(block['parentHash'])

    prev_block = db.query(Block).filter(Block.height == block_height - 1 and Block.chain_id == chain_id).first()
    if prev_block is not None and prev_block.hash != block_prev_hash:
        revertBlock(db, chain_id, block_height - 1)
        return block_height - 1
    elif prev_block is None and block_height != get_config_item(['ethereum', 'min_height']):
        logging.info(f"Block #{chain_id.decode()}-{height-1} not in db - soft reverting.")
        return block_height - 1
    
    current_block = db.query(Block).filter(Block.height == block_height and Block.chain_id == chain_id).first()
    if current_block is not None and current_block.hash == block_hash and current_block.prev_hash == block_prev_hash:
        logging.info(f"Block #{chain_id.decode()}-{height} already in db.")
        return block_height + 1
    elif current_block is not None:
        revertBlock(db, chain_id, block_height)
        return block_height
    
    addBlock(db, chain_id, block_height, block_hash, block_prev_hash)
    return block_height + 1


async def eth_block_follower(chain_name: str, chain_id: bytes):
    db = setup_database()

    web3 = Web3(Web3.HTTPProvider(get_config_item([chain_name, 'rpc_url'])))
    
    latest_block_in_db = db.query(Block).filter(Block.chain_id == chain_id).order_by(Block.height.desc()).first()
    latest_synced_block_height: int = latest_block_in_db.height if latest_block_in_db is not None else get_config_item([chain_name, 'min_height'])
    logging.info(f"Synced peak: {latest_synced_block_height}")

    block_filter = web3.eth.filter('latest')

    latest_mined_block = web3.eth.block_number
    logging.info(f"Quickly syncing to: {latest_mined_block}")

    while latest_synced_block_height <= latest_mined_block:
      latest_synced_block_height = syncBlockUsingHeight(db, web3, chain_id, latest_synced_block_height)
    db.commit()

    logging.info("Quick sync done. Listening for new blocks using filter.")
    while True:
        for block_hash in block_filter.get_new_entries():
            block = web3.eth.get_block(block_hash)
            block_height = block['number']
            latest_synced_block_height = syncBlockUsingHeight(db, web3, chain_id, block_height, block)
            while latest_synced_block_height < block_height + 1:
                latest_synced_block_height = syncBlockUsingHeight(db, web3, chain_id, latest_synced_block_height)
            db.commit()
        time.sleep(1)


@click.command()
@async_func
async def listen():
    await eth_block_follower('ethereum', b"eth")
