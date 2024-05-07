// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "./interfaces/IPortalMessageReceiver.sol";
import "./interfaces/IPortal.sol";
import "./interfaces/IWETH.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/IERC20Permit.sol";

interface ERC20Decimals {
    function decimals() external returns (uint8);
}

/**
 * @title   ERC-20 Bridge for the warp.green protocol
 * @notice  Allows wrapping ERC-20 tokens on Chia and unwrapping them back to the original network using warp.green.
 * @dev     Not compatible with fee-on-transfer or rebasing tokens. The contract assumes 1 token now is 1 token in the future, and that transferring `a` tokens will result to exactly `a` tokens being granted to the recipient (not more, not less).
 */
contract ERC20Bridge is IPortalMessageReceiver {
    /**
     * @notice  When wrapping or unwrapping ERC-20s, a tip is sent to the warp.green protocol. The tip is expressed in basis points.
     * @dev     Tip is calculated as amount * tip / 10000; the remaining amount is sent to the recipient.
     */
    uint16 public immutable tip;

    /**
     * @notice  The address of the warp.green portal contract.
     * @dev     Used as an oracle. The portal usually sits behind a TransparentUpgradeableProxy.
     */
    address public immutable portal;

    /**
     * @notice  The address of the contract used to convert between ether and an equivalent ERC-20. Usually milliETH or WETH.
     * @dev     WETH uses a conversion rate of 1:1, while milliETH uses one of 1000 milliETH = 1 ETH.
     */
    address public immutable iweth;

    /**
     * @notice  How much wei the smallest unit of the wrapped ether ERC-20 translates to.
     * @dev     For example, 1000 milliETH = 1 ETH, so 10^(3+3) wei milliETH (milliETH has 3 decimals) translates to 10^18 wei. The ratio would be 10^12, as 0.001 milliETH (smallest denomination) translates to 10^12 wei = 0.000001 ETH.
     */
    uint64 public immutable wethToEthRatio;

    /**
     * @notice  The chain id of the other chain. Usually "xch", which indicates Chia.
     * @dev     Used to verify the source chain of messages.
     */
    bytes3 public immutable otherChain;

    /**
     * @notice  The hash of the puzzle used to burn CATs associated with an ERC-20 that was locked in this contract. Sender of messages from Chia.
     * @dev     Used to verify the source of messages when they're received.
     */
    bytes32 public burnPuzzleHash;

    /**
     * @notice  The hash of the puzzle used to mint CATs on Chia after they've been transferred to this contract. Message receiver on Chia.
     * @dev     Used as a destination for sent messages.
     */
    bytes32 public mintPuzzleHash;

    /**
     * @notice  ERC20Bridge constructor
     * @param   _tip The percentage (in basis points) used as a tip for the warp.green protocol
     * @param   _portal Address of the warp.green portal contract
     * @param   _iweth Address of the WETH contract or its equivalent
     * @param   _wethToEthRatio The conversion ratio from 1 'wei' of WETH to ETH, considering the difference in decimals
     * @param   _otherChain The chain ID of the destination chain, typically 'xch' for Chia
     * @dev     Initializes contract state with immutable values for gas efficiency
     */
    constructor(
        uint16 _tip,
        address _portal,
        address _iweth,
        uint64 _wethToEthRatio,
        bytes3 _otherChain
    ) {
        tip = _tip;
        portal = _portal;
        iweth = _iweth;
        wethToEthRatio = _wethToEthRatio;
        otherChain = _otherChain;
    }

    /**
     * @notice  Initialize the contract. Should be called in the same transaction as the deployment.
     * @dev     Allows the address of the contract to be determined using CREATE2, as the arguments below depend on the address of this contract. Can only be called once per contract lifetime.
     * @param   _burnPuzzleHash  Chia-side burn puzzle hash. Will be used to set the value of `burnPuzzleHash`.
     * @param   _mintPuzzleHash  Chia-side mint puzzle hash. Will be used to set the value of `mintPuzzleHash`.
     */
    function initializePuzzleHashes(
        bytes32 _burnPuzzleHash,
        bytes32 _mintPuzzleHash
    ) external {
        require(
            burnPuzzleHash == bytes32(0) && mintPuzzleHash == bytes32(0),
            "nope"
        );
        burnPuzzleHash = _burnPuzzleHash;
        mintPuzzleHash = _mintPuzzleHash;
    }

    /**
     * @notice  Receives and processes messages from the warp.green portal
     * @dev     Uses the warp.green Portal contract as an oracle; verifies message and handles the unwrapping process.
     * @param   _source_chain  Message source chain id (e.g., "xch").
     * @param   _source  Message source (puzzle hash).
     * @param   _contents  Message contents - asset contract, receiver, and mojo amount.
     */
    function receiveMessage(
        bytes32 /* _nonce */,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] calldata _contents
    ) external {
        require(
            msg.sender == portal &&
                _source == burnPuzzleHash &&
                _source_chain == otherChain,
            "!msg"
        );

        address assetContract = address(uint160(uint256(_contents[0])));
        address receiver = address(uint160(uint256(_contents[1])));
        uint256 amount = uint256(_contents[2]);

        amount = (amount * 10 ** (ERC20Decimals(assetContract).decimals() - 3)); // transform from mojos to ETH wei

        uint256 transferTip = (amount * tip) / 10000;

        if (assetContract != iweth) {
            SafeERC20.safeTransfer(
                IERC20(assetContract),
                receiver,
                amount - transferTip
            );
            SafeERC20.safeTransfer(IERC20(assetContract), portal, transferTip);
        } else {
            IWETH(iweth).withdraw(amount);

            payable(receiver).transfer((amount - transferTip) * wethToEthRatio);
            payable(portal).transfer(transferTip * wethToEthRatio);
        }
    }

    /**
     * @notice  Bridges ERC-20 tokens to Chia via the warp.green protocol
     * @dev     Transfers tokens to this contract, redirects portal tip, and send message for tokens to be minted on Chia.
     * @param   _assetContract  Address of the ERC-20 token to bridge.
     * @param   _receiver  Receiver of the wrapped tokens on Chia, given as a puzzle hash. Usually the decoded bech32m address taken from the wallet.
     * @param   _mojoAmount  Amount to bridge to Chia, in mojos (1 CAT = 10^3 mojos). For example, 1 would mean 0.001 CAT tokens, equivalent to 0.001 ERC-20 tokens.
     */
    function bridgeToChia(
        address _assetContract,
        bytes32 _receiver,
        uint256 _mojoAmount // on Chia
    ) external payable {
        require(msg.value == IPortal(portal).messageToll(), "!toll");

        _handleBridging(
            _assetContract,
            true,
            _receiver,
            _mojoAmount,
            msg.value,
            10 ** (ERC20Decimals(_assetContract).decimals() - 3)
        );
    }

    /**
     * @notice  Bridges native Ether to Chia by first wrapping it into milliETH (or WETH).
     * @dev     Wraps ether into an ERC-20, sends portal tip, and sends a message to mint tokens on Chia.
     * @param   _receiver  Receiver puzzle hash for the wrapped tokens.
     */
    function bridgeEtherToChia(bytes32 _receiver) external payable {
        uint256 messageToll = IPortal(portal).messageToll();

        uint256 amountAfterToll = msg.value - messageToll;
        require(
            amountAfterToll >= wethToEthRatio &&
                amountAfterToll % wethToEthRatio == 0,
            "!amnt"
        );

        IWETH(iweth).deposit{value: amountAfterToll}();

        uint256 wethToMojosFactor = 10 ** (ERC20Decimals(iweth).decimals() - 3);

        _handleBridging(
            iweth,
            false,
            _receiver,
            amountAfterToll / wethToEthRatio / wethToMojosFactor,
            messageToll,
            wethToMojosFactor
        );
    }

    /**
     * @notice  Bridges ERC-20 tokens to Chia with a permit allowing token spend
     * @dev     Uses ERC-20 permit for gas-efficient token approval and transfer in a single transaction.
     * @param   _assetContract  Address of the ERC-20 token to bridge.
     * @param   _receiver  Receiver puzzle hash for the wrapped tokens.
     * @param   _amount  Amount to bridge to Chia, in mojos. For example, 1 would mean 0.001 CAT tokens, equivalent to 0.001 ERC-20 tokens.
     * @param   _deadline  Permit deadline.
     * @param   _v  Permit signature v value.
     * @param   _r  Permit signature r value.
     * @param   _s  Permit signature s value.
     */
    function bridgeToChiaWithPermit(
        address _assetContract,
        bytes32 _receiver,
        uint256 _amount, // on Chia
        uint256 _deadline,
        uint8 _v,
        bytes32 _r,
        bytes32 _s
    ) external payable {
        require(msg.value == IPortal(portal).messageToll(), "!toll");
        uint256 factor = 10 ** (ERC20Decimals(_assetContract).decimals() - 3);

        IERC20Permit(_assetContract).permit(
            msg.sender,
            address(this),
            _amount * factor,
            _deadline,
            _v,
            _r,
            _s
        );

        _handleBridging(
            _assetContract,
            true,
            _receiver,
            _amount,
            msg.value,
            factor
        );
    }

    /**
     * @notice  Internal function used to craft and send message acknowledging tokens were locked on this chain.
     * @dev     Tip is also calculated and transferred to the portal in this function.
     * @param   _assetContract  Address of the ERC-20 token to bridge.
     * @param   _transferAsset  Whether to transfer the asset to this contract or not. If false, the tokens must already be owned by this contract.
     * @param   _receiver  Receiver puzzle hash for the wrapped tokens.
     * @param   _amount  Amount of CAT tokens to be minted on Chia (in mojos).
     * @param   _messageToll Message toll in wei required by the portal to relay the message.
     * @param   _mojoToTokenFactor  A power of 10 to convert from CAT amount (mojos) to the ERC-20 token's smallest unit.
     */
    function _handleBridging(
        address _assetContract,
        bool _transferAsset,
        bytes32 _receiver,
        uint256 _amount, // WARNING: in CAT mojos
        uint256 _messageToll,
        uint256 _mojoToTokenFactor
    ) internal {
        uint256 transferTip = (_amount * tip) / 10000;

        bytes32[] memory message = new bytes32[](3);
        message[0] = bytes32(uint256(uint160(_assetContract)));
        message[1] = _receiver;
        message[2] = bytes32(_amount - transferTip);

        if (_transferAsset) {
            SafeERC20.safeTransferFrom(
                IERC20(_assetContract),
                msg.sender,
                address(this),
                _amount * _mojoToTokenFactor
            );
        }
        SafeERC20.safeTransfer(
            IERC20(_assetContract),
            portal,
            transferTip * _mojoToTokenFactor
        );

        IPortal(portal).sendMessage{value: _messageToll}(
            otherChain,
            mintPuzzleHash,
            message
        );
    }

    /**
     * @notice  Function that handles incoming ether transfers. Do not simply send ether to this contract.
     * @dev     The 'require' should prevent user errors.
     */
    receive() external payable {
        require(msg.sender == iweth, "!sender");
    }
}
