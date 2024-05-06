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
1. Clone this repository and enter the `cli` directory by running:

    ```bash
    git clone https://github.com/warpdotgreen/cli.git -b master
    ```
    ```bash
    cd cli
    ```
2. Ask yourself if it is worth it. This repo comes with a dockerfile, so you can simply do:
    ```bash
    docker build . -t cli
    echo '{}' > config.json
    touch data.db
    docker run -v "$(pwd)"/config.json:/app/config.json -v "$(pwd)"/data.db:/app/data.db cli --help
    ```

3. Ensure prerequisite software is installed. This repo has been tested with `python 3.10/3.11` and `nodejs v18`. If you have a different node version, uninstall and install the correct version via:

    ```bash
    curl -sL https://deb.nodesource.com/setup_18.x -o /tmp/nodesource_setup.sh
    chmod +x /tmp/nodesource_setup.sh && /tmp/nodesource_setup.sh
    ```

4. Create and activate a virtual environment:

      ```bash
      python3 -m venv venv
      ```
  
5. Install all required packages:

    ```bash
    pip install --extra-index-url https://pypi.chia.net/simple/ chia-dev-tools==1.2.5
    pip install -r requirements.txt
    ```
    
6. Compile EVM contracts

    Create `hardhat.config.ts` in the root directory:

    ```base
    cp hardhat.config.example.ts hardhat.config.ts
    ```

    Then, run:

    ```bash
    npm i
    npx hardhat compile
    ```

## Test

The repository includes several tests. To run tests:

    ```bash
    ./test.sh
    npx hardhat test
    ```

To check contract test coverage: `npx hardhat coverage`.

## License

This repository is licensed under the MIT License. For more details, please see the [LICENSE](LICENSE) file.
