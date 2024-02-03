# XCH-Eth Portal & Bridge
### (and the other way around)

A Proof-of-Authority based cross-chain messaging protocol - a portal.

## Prerequisites

* Ubuntu/Debian Linux, Windows, or MacOS (so far, most testing has been on Linux)
* Python 3.10 or later

## Install

1. Install NPM:

    ```bash
    pip install npm
    ```

2. Clone the `bridge` GitHub repository (you may need to change the branch name) and enter the `bridge` directory by running:

    ```bash
    git clone https://github.com/Yakuhito/bridge.git -b master
    ```
    ```bash
    cd bridge
    ```

3. Create and activate a virtual environment:

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
  
4. Install all required packages:

    ```bash
    pip install -r requirements.txt
    ```

5. Install the bridge:

    ```bash
    npm install .
    ```

## Test

The bridge includes several Pytest tests. To run them:

  * Linux/MacOS
    
    ```bash
    sh test.sh [testname]
    ```

  * Windows

    ```powershell
    .\test.bat [testname]
    ```
   
To run a specific test, include part of the test's name in `[testname]`. For example, `sh test.sh healthz` (Linux/MacOS) or `.\test.bat healthz` (Windows) will run only test(s) containing the word `healthz`

To run all tests, don't include `[testname]`. For example,  `sh test.sh` (Linux/MacOS) or `.\test.bat` (Windows) will run all tests.

## Architecture

To connect the Chia and Ethereum blockchains, a trusted set of parties (validators) is needed. These parties each observe messages on one chain and relay it to the other. This repository also contains the code required to enable bridging tokens and native assets from Ethereum to Chia - the contracts are immutable, and rely on the bridge as an oracle. A fee is also given to the owner of the immutable contracts, presumably the bridge owners.

To ensure each message is unique, each side of the portal assigns a nonce (a unique, increasing integer on Ethereum, a coin id on Chia) to each message. On the other side, the user has the ability to execute messages out-of-order, but can only use each message exactly once. A deadline can be used to specify an expiry timestamp after which a message will become invalid.

Portal contracts are upgradeable by the validators to enable new functionality to be developed.

On Chia, messages are picked up by looking for the following output condition:

```
(list CREATE_COIN
  [bridge_specific_puzzle_hash]
  [amount]
  ([source_type] [destination_chain] [destination_type] [destination_info] [deadline] . [content])
)
```

Special thanks to acevail for the idea above, which greatly simplified the design :)
