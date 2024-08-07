from typing import Any, Callable
from commands.models import *
from commands.config import get_config_item
from sqlalchemy import and_
from eth_account.messages import encode_typed_data
from commands.followers.sig import encode_signature
from web3 import AsyncWeb3
from web3.providers.async_rpc import AsyncHTTPProvider
import aiohttp
import asyncio
import logging
import asyncio
import json
import sys

async def custom_retry_middleware(make_request: Callable[[str, Any], Any], web3: AsyncWeb3) -> Callable[[str, Any], Any]:
    async def middleware(method: str, params: Any) -> Any:
        while True:
            try:
                return await make_request(method, params)
            except aiohttp.ClientResponseError as e:
                logging.error(f"HTTP error when calling RPC: {e.status} {e.message}; retrying in 5s...")
                await asyncio.sleep(5)

    return middleware


class EthereumFollower:
    chain: str
    chain_id: bytes
    sign_min_height: int
    portal_address: str
    private_key: str
    is_optimism: bool
    send_sig: any
    max_query_block_limit: int = 1000
    last_safe_height: int = 0
    l1_block_contract_address: str
    l1_block_contract: any = None
    
    def __init__(self, chain: str, is_optimism: bool, send_sig: any):
        self.chain = chain
        self.chain_id = chain.encode()
        self.sign_min_height = get_config_item([self.chain, 'sign_min_height'])
        self.portal_address = get_config_item([self.chain, 'portal_address'])
        self.private_key = get_config_item([self.chain, 'my_hot_private_key'])
        self.is_optimism = is_optimism
        self.syncing = True
        if self.is_optimism:
          self.l1_block_contract_address = get_config_item([self.chain, 'l1_block_contract_address'])
        
        self.send_sig = send_sig


    def getDb(self):
       return setup_database()
    

    def getWeb3(self) -> AsyncWeb3:
        headers = {
            'User-Agent': 'requests/1.0.0',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        web3 = AsyncWeb3(AsyncHTTPProvider(get_config_item([self.chain, 'rpc_url']), request_kwargs={ 'headers': headers }))
        web3.middleware_onion.inject(custom_retry_middleware, name='custom_retry_middleware', layer=0)

        return web3
    

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
          sig=b'',
      )


    async def getBlockNumber(self, web3: AsyncWeb3) -> int:
      while True:
        try:
           return await web3.eth.block_number
        except:
              logging.error(f"{self.chain_id.decode()} follower: could not get block number; trying again in 5s...", exc_info=True)
              await asyncio.sleep(5)


    # warning: only use in the 'messageListener' thread
    async def getEventByIntNonce(self, web3, contract, nonce: int, start_height: int):
      if self.last_safe_height <= 0:
          self.last_safe_height = start_height

      nonce_hex = "0x" + self.nonceIntToBytes(nonce)
      query_start_height = max(self.last_safe_height, start_height) # cache

      while True:
        current_block_height = await self.getBlockNumber(web3)

        if query_start_height >= current_block_height:
            return None

        query_end_height = min(query_start_height + self.max_query_block_limit - 1, current_block_height)
        
        logging.info(f"Searching for {self.chain_id.decode()}-{nonce} from {query_start_height} to {query_end_height}...")
        logs = await contract.events.MessageSent().get_logs(
            fromBlock=query_start_height,
            toBlock=query_end_height,
            argument_filters={"nonce": nonce_hex},
        )

        logs = [_ for _ in logs]
        if len(logs) > 0:
           return logs[0]
        
        # self.max_query_block_limit * 3 // 4 is much more than the expected reorg window
        self.last_safe_height = max(self.last_safe_height, query_end_height - self.max_query_block_limit * 3 // 4)
        query_start_height = query_end_height + 1
    

    async def messageListener(self):
      db = self.getDb()
      web3 = self.getWeb3()

      portal_contract_abi = json.loads(open("artifacts/contracts/Portal.sol/Portal.json", "r").read())["abi"]
      
      latest_message_in_db = db.query(Message).filter(
         Message.source_chain == self.chain_id
      ).order_by(Message.nonce.desc()).first()

      latest_synced_nonce_int: int = int(latest_message_in_db.nonce.hex()[2:], 16) if latest_message_in_db is not None else 0
      last_synced_height: int = latest_message_in_db.block_number if latest_message_in_db is not None else get_config_item([self.chain, 'min_height'])
      
      logging.info(f"Last synced nonce: {self.chain_id.decode()}-{latest_synced_nonce_int}")

      contract = web3.eth.contract(address=self.portal_address, abi=portal_contract_abi)

      while True:
        try:
            next_message_event = await self.getEventByIntNonce(web3, contract, latest_synced_nonce_int + 1, last_synced_height - 1)

            if next_message_event is None:
                logging.info(f"{self.chain_id.decode()} message listener: all on-chain messages synced; listening for new messages.")
                
                while next_message_event is None:
                  await asyncio.sleep(30)
                  next_message_event = await self.getEventByIntNonce(web3, contract, latest_synced_nonce_int + 1, last_synced_height - 1)

                continue

            event_block_number = next_message_event['blockNumber']

            if not self.is_optimism:
                # L1 - mainnet confirmations can be obtained from block number
                eth_block_number = await self.getBlockNumber(web3)

                while event_block_number + self.sign_min_height > eth_block_number:
                    await asyncio.sleep(5)
                    eth_block_number = await self.getBlockNumber(web3)
                    logging.info(f"{self.chain_id.decode()} message listener: Waiting for block {event_block_number + self.sign_min_height} to confirm message; current block: {eth_block_number}")
            else:
                # L2 - https://jumpcrypto.com/writing/bridging-and-finality-op-and-arb/
                # in short, since we're trusting the sequencer anyway, we can also trust:
                # https://github.com/ethereum-optimism/optimism/blob/develop/packages/contracts-bedrock/src/L2/L1Block.sol/
                # to relay L1 block numbers accurately
                # self.sign_min_height is then the min. number of confirmations in L1 blocks
                # you can find the address for the contract at https://docs.base.org/docs/base-contracts
                block = await web3.eth.get_block(event_block_number, full_transactions=True)
                relevant_tx = None
                for tx in block.transactions:
                    if tx.to and tx.to == self.l1_block_contract_address:
                        relevant_tx = tx
                        break
                
                if relevant_tx is None:
                    logging.error(f"{self.chain_id.decode()} message listener: could not find L1Block update tx for block {event_block_number}; sleeping 30s and retrying...")
                    await asyncio.sleep(30)
                    continue
                
                
                raw_input = bytes(relevant_tx.input)
                # https://github.com/ethereum-optimism/optimism/blob/develop/packages/contracts-bedrock/src/L2/L1Block.sol/#L112
                input_offset = 28
                event_l1_block_number = int(raw_input[input_offset:input_offset + 8].hex(), 16)
                logging.info(f"{self.chain_id.decode()} message listener: Confirming message with L1 block number {event_l1_block_number} (L2: {event_block_number}) for {self.chain_id.decode()}-{next_message_event['args']['nonce'].hex()}")
                
                if self.l1_block_contract is None:
                  self.l1_block_contract = web3.eth.contract(
                    address=AsyncWeb3.to_checksum_address(self.l1_block_contract_address),
                    abi=open("l1_block_abi.json", "r").read()
                  )

                l1_block_number = await self.l1_block_contract.functions.number().call()
                while event_l1_block_number + self.sign_min_height > l1_block_number:
                    await asyncio.sleep(10)
                    l1_block_number = await self.l1_block_contract.functions.number().call()
                    logging.info(f"{self.chain_id.decode()} message listener: Current L1 block number is {l1_block_number}")

            next_message_event_copy = await self.getEventByIntNonce(web3, contract, latest_synced_nonce_int + 1, last_synced_height - 1)
            if next_message_event_copy is None:
                logging.info(f"{self.chain_id.decode()} message listener: could not get message event again; assuming reorg and retrying...")
                last_synced_height -= self.max_query_block_limit
                self.last_safe_height -= 10 * self.max_query_block_limit
                continue
            
            next_message = self.eventObjectToMessage(next_message_event)
            next_message_copy = self.eventObjectToMessage(next_message_event_copy)

            if next_message.nonce != next_message_copy.nonce or next_message.source != next_message_copy.source or next_message.destination_chain != next_message_copy.destination_chain or next_message.destination != next_message_copy.destination or next_message.contents != next_message_copy.contents or next_message.block_number != next_message_copy.block_number: 
                logging.info(f"{self.chain_id.decode()} message listener: message event mismatch; assuming reorg and retrying...")
                last_synced_height -= self.max_query_block_limit
                self.last_safe_height -= 10 * self.max_query_block_limit
                continue
            
            logging.info(f"{self.chain_id.decode()} message listener: Adding message #{self.chain_id.decode()}-{next_message.nonce.hex()}")
            db.add(next_message)
            db.commit()

            latest_synced_nonce_int += 1
            last_synced_height = event_block_number
        except:
            logging.exception(f"{self.chain_id.decode()} message listener: Exception occurred", exc_info=True)
            sys.exit(1)
  
    async def signMessage(self, db, web3: AsyncWeb3, message: Message):
        domain = {
            'name': 'warp.green Portal',
            'version': '1',
            'chainId': await web3.eth.chain_id,
            'verifyingContract': self.portal_address,
        }
        types = {
            'Message': [
                {'name': 'nonce', 'type': 'bytes32'},
                {'name': 'source_chain', 'type': 'bytes3'},
                {'name': 'source', 'type': 'bytes32'},
                {'name': 'destination', 'type': 'address'},
                {'name': 'contents', 'type': 'bytes32[]'},
            ]
        }

        destination = message.destination.hex()[-40:]
        if len(destination) < 40:
           destination = "0" * (40 - len(destination)) + destination
        destination = AsyncWeb3.to_checksum_address("0x" + destination)
        encoded_data = encode_typed_data(
            domain,
            types,
            {
                'nonce': '0x' + message.nonce.hex(),
                'source_chain': '0x' + message.source_chain.hex(),
                'source': '0x' + message.source.hex(),
                'destination': destination,
                'contents': ['0x' + content.hex() for content in split_message_contents(message.contents)],
            }
        )
        signed_message = web3.eth.account.sign_message(encoded_data, private_key=self.private_key)

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

        self.send_sig(message.sig.decode())


    async def messageSigner(self):
      db = self.getDb()
      web3 = self.getWeb3()

      while True:
          try:
              messages = []
              messages = db.query(Message).filter(and_(
                  Message.destination_chain == self.chain_id,
                  Message.sig == b''
              )).all()

              for message in messages:
                  await self.signMessage(db, web3, message)
                  db.commit()

              await asyncio.sleep(5)
          except:
              logging.exception(f"{self.chain_id.decode()} message signer: Exception occurred", exc_info=True)
              sys.exit(1)


    def run(self, loop):
      self.loop = loop

      self.loop.create_task(self.messageListener())
      self.loop.create_task(self.messageSigner())

    async def wait_for_node(self, log_startup_connection_errors: bool):
      while True:
        try:
            web3 = self.getWeb3()
            await web3.eth.block_number

            return
        except:
            logging.info(f"Could not connect to {self.chain} node; trying again in 10s", exc_info=log_startup_connection_errors)
            await asyncio.sleep(10)
