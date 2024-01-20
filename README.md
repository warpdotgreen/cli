# XCH-Eth Bridge
### (and the other way around)

A Proof-of-Authority based cross-chain messaging protocol.

## Architecture

To connect the Chia and Ethereum blockchains, a trusted set of parties (validators) is needed. These parties each observe messages on one chain and relay it to the other. This repository also contains the code required to enable bridging tokens and native assets from one blockchain to the other - the contracts are immutable, and rely on the bridge as an oracle.

To ensure each message is unique, each side of the bridge assigns a nonce (a unique, increasing integer) to each message. On the other side, the user has the ability to execute messages out-of-order, but can only use each message exactly once. 

Bridge contracts are upgradeable by the validators.
