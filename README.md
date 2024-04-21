# warp.green

A cross-chain messaging protocol - a portal betwen blockchains.

## Architecture

Note: Technical overview available [here](https://pitch.com/v/warpdotgreen-xwmj7r).

To connect the Chia and Ethereum/Base blockchains, a trusted set of parties (validators) is needed. These parties independently observe messages on the supported chains and generate a signature attesting the details. On the destination chain, a puzzle or contract accepts a majority of signatures and acts as an oracle, effectively allowing apps to send and receive messages between chains.

This repository also contains the code required to enable bridging tokens and native assets between the supported chains. The poral (trusted oracle) is controlled by a validator multisig and can be upgraded at any time. The bridge contracts and puzzles are immutable, and rely on the bridge as an oracle. A 0.3% tip is given to the portal for each transaction.

To ensure uniqueness, each side of the portal assigns a nonce (a unique, increasing integer on Ethereum; a coin id on Chia) to each message. On the other side, the user has the ability to execute messages out-of-order, but can only use each message exactly once.

On Chia, messages are picked up by looking for the following output condition:

```
(list CREATE_COIN
  [bridging_puzzle_hash]
  [bridging_fee_or_more]
  ([destination_chain] [destination] . [content])
)
```

## Install

1. Clone theGitHub repository and enter the `bridge` directory by running:

    ```bash
    git clone https://github.com/warpdotgreen/bridge.git -b master
    ```
    ```bash
    cd bridge
    ```

2. Create and activate a virtual environment:

    * Linux/MacOS

      ```bash
      python3 -m venv venv
      ```
      ```bash
      . ./venv/bin/activate
      ```

    * Windows

      ```powershell
      python -m venv venv
      ```
      ```powershell
      .\venv\Scripts\Activate.ps1
      ```
  
3. Install all required packages:

    ```bash
    pip install --extra-index-url https://pypi.chia.net/simple/ chia-dev-tools
    pip install --extra-index-url https://pypi.chia.net/simple/ chia-blockchain==2.2.0
    pip install web3
    pip install nostr-sdk
    pip install asyncio
    pip install sqlalchemy
    pip install qrcode
    ```
    
4. Compile EVM contracts

    Create `hardhat.config.ts` in the root directory with the following contents:

    ```js
    import { HardhatUserConfig } from "hardhat/config";
    import "@nomicfoundation/hardhat-toolbox";
    import "hardhat-gas-reporter";

    const config: HardhatUserConfig = {
      solidity: {
        version: "0.8.20",
        settings: {
          optimizer: {
            enabled: true,
            runs: 200,
          },
        },
      },
      etherscan: {
        customChains: [
          {
            network: "base_sepolia",
            chainId: 84532,
            urls: {
              apiURL: "https://api-sepolia.basescan.org/api",
              browserURL: "https://sepolia.basescan.org/"
            }
          }
        ]
      },
      gasReporter: {
        currency: 'USD',
        L1: "ethereum",
        L2: "base",
      },
    };

    export default config;
    ```

    Then, run:

    ```bash
    npm i --force
    npx hardhat compile
    ```

## Test

The repository includes several tests. To run puzzle tests:

  * Linux/MacOS
    
    ```bash
    sh test.sh
    ```

  * Windows

    ```powershell
    .\test.bat
    ```
   
To run a specific test, append part of the test's name to the command. For example, `sh test.sh healthz` (Linux/MacOS) or `.\test.bat healthz` (Windows) will run only test(s) containing the word `healthz`. If no name is included, all tests will be run.

To run contract tests, you can simply use `npx hardhat test`.

## License

This repository is licensed under the MIT License. For more details, please see the [LICENSE](LICENSE) file.
