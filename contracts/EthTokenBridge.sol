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

contract EthTokenBridge is IPortalMessageReceiver {
    uint256 public immutable tip; // (tip / 10000) % tip
    address public immutable portal;
    address public immutable iweth;
    uint256 public immutable wethToEthRatio; // in wei - how much wei one 'wei' of WETH translates to
    // for example: 1000 milliETH = 1 ETH, so 10^(3+3) wei milliETH (3 decimals) translates to 10^18 wei -> ratio is 10^12
    // amount_weth * wethToEthRatio = eth amount to pay out
    bytes3 public immutable otherChain;

    bytes32 public burnPuzzleHash;
    bytes32 public mintPuzzleHash;

    constructor(
        uint256 _tip,
        address _portal,
        address _iweth,
        uint256 _wethToEthRatio,
        bytes3 _otherChain
    ) {
        tip = _tip;
        portal = _portal;
        iweth = _iweth;
        wethToEthRatio = _wethToEthRatio;
        otherChain = _otherChain;
    }

    // should be called in the same tx/block as the creation tx
    // allows the address to be determined using CREATE2
    // w/o depending on puzzle hashes (since those already depend
    // on the address of this contract)
    function initializePuzzleHashes(
        bytes32 _burnPuzzleHash,
        bytes32 _mintPuzzleHash
    ) public {
        require(
            burnPuzzleHash == bytes32(0) && mintPuzzleHash == bytes32(0),
            "nope"
        );
        burnPuzzleHash = _burnPuzzleHash;
        mintPuzzleHash = _mintPuzzleHash;
    }

    function receiveMessage(
        bytes32 /* _nonce */,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] memory _contents
    ) public {
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

    function bridgeToChia(
        address _assetContract,
        bytes32 _receiver,
        uint256 _mojoAmount // on Chia
    ) public payable {
        require(msg.value == IPortal(portal).messageFee(), "!fee");

        _handleBridging(
            _assetContract,
            true,
            _receiver,
            _mojoAmount,
            msg.value,
            10 ** (ERC20Decimals(_assetContract).decimals() - 3)
        );
    }

    function bridgeEtherToChia(bytes32 _receiver) public payable {
        uint256 messageFee = IPortal(portal).messageFee();

        uint256 amountAfterFee = msg.value - messageFee;
        require(
            amountAfterFee >= wethToEthRatio &&
                amountAfterFee % wethToEthRatio == 0,
            "!amnt"
        );

        IWETH(iweth).deposit{value: amountAfterFee}();

        uint256 wethToMojosFactor = 10 ** (ERC20Decimals(iweth).decimals() - 3);

        _handleBridging(
            iweth,
            false,
            _receiver,
            amountAfterFee / wethToEthRatio / wethToMojosFactor,
            messageFee,
            wethToMojosFactor
        );
    }

    function bridgeToChiaWithPermit(
        address _assetContract,
        bytes32 _receiver,
        uint256 _amount, // on Chia
        uint256 _deadline,
        uint8 _v,
        bytes32 _r,
        bytes32 _s
    ) public payable {
        require(msg.value == IPortal(portal).messageFee(), "!fee");
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

    function _handleBridging(
        address _assetContract,
        bool _transferAsset,
        bytes32 _receiver,
        uint256 _amount, // WARNING: in CAT mojos
        uint256 _messageFee,
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

        IPortal(portal).sendMessage{value: _messageFee}(
            otherChain,
            mintPuzzleHash,
            message
        );
    }

    receive() external payable {
        require(msg.sender == iweth, "!sender");
    }
}
