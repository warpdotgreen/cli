from commands.models import *
from commands.config import get_config_item
from sqlalchemy import and_
from typing import Tuple
from eth_account.messages import encode_defunct
from commands.followers.sig import encode_signature, decode_signature
from web3 import Web3
import logging
import json
import asyncio

class EthereumFollower:
    chain: str
    chain_id: bytes
    sign_min_height: int
    private_key: str
    
    def __init__(self, chain: str):
        self.chain = chain
        self.chain_id = chain.encode()
        self.sign_min_height = get_config_item([self.chain, 'sign_min_height'])
        self.private_key = get_config_item([self.chain, 'my_hot_private_key'])


    def getDb(self):
       return setup_database()
    

    def getWeb3(self):
       return Web3(Web3.HTTPProvider(get_config_item([self.chain, 'rpc_url'])))
    

    def nonceIntToBytes(self, nonceInt: int) -> bytes:
      s = hex(nonceInt)[2:]
      return (64 - len(s)) * "0" + s
    

    def eventObjectToMessage(self, event) -> Message:
      source = bytes.fromhex(event['args']['source'][2:])

      return Message(
          nonce=event['args']['nonce'],
          source_chain=self.chain_id,
          source=b"\x00" * (64 - len(source)) + source,
          destination_chain=event['args']['destination_chain'],
          destination=event['args']['destination'],
          contents=join_message_contents(event['args']['contents']),
          block_number=event['blockNumber'],
          confirmed_for_signing=True,
          sig=b'',
      )

    
    def getEventByIntNonce(self, contract, nonce: int, start_height: int):
      one_event_filter = contract.events.MessageSent.create_filter(
          fromBlock=start_height,
          toBlock='latest',
          argument_filters={'nonce': "0x" + self.nonceIntToBytes(nonce)}
      )
      entries = one_event_filter.get_all_entries()

      if len(entries) == 0:
          return None
      
      return entries[0]
    

    async def messageListener(self):
      db = self.getDb()
      web3 = self.getWeb3()

      portal_contract_abi = json.loads(open("artifacts/contracts/Portal.sol/Portal.json", "r").read())["abi"]
      portal_contract_address = get_config_item([self.chain, 'portal_address'])
      
      latest_message_in_db = db.query(Message).filter(
         Message.source_chain == self.chain_id
      ).order_by(Message.nonce.desc()).first()

      latest_synced_nonce_int: int = int(latest_message_in_db.nonce.hex()[2:], 16) if latest_message_in_db is not None else 0
      last_synced_height: int = latest_message_in_db.block_number if latest_message_in_db is not None else get_config_item([self.chain, 'min_height'])
      
      logging.info(f"Last synced nonce: {self.chain_id.decode()}-{latest_synced_nonce_int}")

      contract = web3.eth.contract(address=portal_contract_address, abi=portal_contract_abi)

      while True:
         next_message_event = self.getEventByIntNonce(contract, latest_synced_nonce_int + 1, last_synced_height - 1)

         if next_message_event is None:
            logging.info(f"{self.chain_id.decode()} message listener: all on-chain messages synced; listening for new messages.")
            
            event_filter = contract.events.MessageSent.create_filter(fromBlock='latest')
            new_message_found = False
            while not new_message_found:
              for _ in event_filter.get_new_entries():
                  new_message_found = True
              
              await asyncio.sleep(30)

            event_filter.uninstall()
            continue

         event_block_number = next_message_event['blockNumber']
         eth_block_number = web3.eth.block_number

         while event_block_number + self.sign_min_height > eth_block_number:
            await asyncio.sleep(5)
            eth_block_number = web3.eth.block_number

         next_message_event_copy = self.getEventByIntNonce(contract, latest_synced_nonce_int + 1, last_synced_height - 1)
         if next_message_event_copy is None:
            logging.info(f"{self.chain_id.decode()} message listener: could not get message event again; assuming reorg and retrying...")
            last_synced_height -= 1000
            continue
         
         next_message = self.eventObjectToMessage(next_message_event)
         next_message_copy = self.eventObjectToMessage(next_message_event_copy)

         if next_message.nonce != next_message_copy.nonce or next_message.source != next_message_copy.source or next_message.destination_chain != next_message_copy.destination_chain or next_message.destination != next_message_copy.destination or next_message.contents != next_message_copy.contents or next_message.block_number != next_message_copy.block_number: 
            logging.info(f"{self.chain_id.decode()} message listener: message event mismatch; assuming reorg and retrying...")
            continue
         
         logging.info(f"{self.chain_id.decode()} message listener: Adding message #{self.chain_id.decode()}-{next_message.nonce.hex()}")
         db.add(next_message)
         db.commit()

         latest_synced_nonce_int += 1
         last_synced_height = event_block_number

  
    async def signMessage(self, db, web3: Web3, message: Message):
      encoded_message = web3.solidity_keccak(
          ['bytes32', 'bytes3', 'bytes32', 'address', 'bytes32[]'],
          [
              message.nonce,
              message.source_chain,
              message.source,
              Web3.to_checksum_address("0x" + message.destination[-40:].hex()),
              split_message_contents(message.contents)
          ]
      )
      signed_message = web3.eth.account.sign_message(
         encode_defunct(encoded_message),
         self.private_key
      )
      
      # uint8(v), bytes32(r), bytes32(s)
      v = hex(signed_message.v)[2:]
      if len(v) < 2:
          v = "0" * (2 - len(v)) + v
      r = hex(signed_message.r)[2:]
      if len(r) < 64:
         r = (64 - len(r)) * "0" + r
      s = hex(signed_message.s)[2:]
      if len(s) < 64:
          s = (64 - len(s)) * "0" + s
      sig = bytes.fromhex(v + r + s)

      logging.info(f"{self.chain} Signer: {message.source_chain.decode()}-{message.nonce.hex()}: Raw signature: {sig.hex()}")

      message.sig = encode_signature(
          message.source_chain,
          message.destination_chain,
          message.nonce,
          None,
          sig
      ).encode()
      db.commit()
      logging.info(f"{self.chain} Signer: {message.source_chain.decode()}-{message.nonce.hex()}: Signature: {message.sig.decode()}")

      # todo: replace with nostr
      open("messages.txt", "a").write(message.sig.decode() + "\n")


    async def messageSigner(self):
      db = self.getDb()
      web3 = self.getWeb3()

      while True:
          messages = []
          messages = db.query(Message).filter(and_(
              Message.destination_chain == self.chain_id,
              Message.sig == b'',
              Message.confirmed_for_signing.is_(True)
          )).all()

          for message in messages:
              await self.signMessage(db, web3, message)
              db.commit()

          await asyncio.sleep(5)


    def run(self, loop):
      self.loop = loop

      self.loop.create_task(self.messageListener())
      self.loop.create_task(self.messageSigner())
