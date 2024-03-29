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
    uint256 public immutable fee = 30; // (fee / 10000) % fee
    address public immutable portal;
    address public immutable iweth;
    uint256 public immutable wethToEthRatio; // in wei - how much wei one 'wei' of WETH translates to
    // for example: 1000 milliETH = 1 ETH, so 10^(3+3) wei milliETH (3 decimals) translates to 10^18 wei -> ratio is 10^12
    // amount_weth * wethToEthRatio = eth amount to pay out

    bytes32 public immutable burnPuzzleHash;
    bytes32 public immutable mintPuzzleHash;

    constructor(
        address _portal,
        address _feeManager,
        address _iweth,
        uint256 _wethToEthRatio
    ) Ownable(_feeManager) {
        portal = _portal;
        iweth = _iweth;
        wethToEthRatio = _wethToEthRatio;
        chiaSideBurnPuzzle = bytes32(0);
        chiaSideMintPuzzle = bytes32(0);
    }

    function initializePuzzleHashes(
        bytes32 _chiaSideBurnPuzzle,
        bytes32 _chiaSideMintPuzzle
    ) public onlyOwner {
        require(
            chiaSideBurnPuzzle == bytes32(0) &&
                chiaSideMintPuzzle == bytes32(0),
            "nope"
        );
        chiaSideBurnPuzzle = _chiaSideBurnPuzzle;
        chiaSideMintPuzzle = _chiaSideMintPuzzle;
    }

    // fee between 0% and 10%
    function updateFee(uint256 _newFee) public onlyOwner {
        require(_newFee <= 1000, "fee too high");
        fee = _newFee;
    }

    function receiveMessage(
        bytes32 /* _nonce */,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] memory _contents
    ) public {
        require(msg.sender == portal, "!portal");
        require(
            _source == chiaSideBurnPuzzle && _source_chain == bytes3("xch"),
            "!source"
        );

        address assetContract = address(uint160(uint256(_contents[0])));
        address receiver = address(uint160(uint256(_contents[1])));
        uint256 amount = uint256(_contents[2]);

        amount = (amount * 10 ** (ERC20Decimals(assetContract).decimals() - 3)); // transform from mojos to ETH wei

        uint256 transferFee = (amount * fee) / 10000;
        fees[assetContract] += transferFee;
        amount -= transferFee;

        if (assetContract != iweth) {
            SafeERC20.safeTransfer(IERC20(assetContract), receiver, amount);
        } else {
            IWETH(iweth).withdraw(amount);
            payable(receiver).transfer(amount * wethToEthRatio);
        }
    }

    receive() external payable {}

    function bridgeToChia(
        address _assetContract,
        bytes32 _receiver,
        uint256 _amount // on Chia
    ) public payable {
        require(msg.value == IPortal(portal).messageFee(), "!fee");

        _handleBridging(
            _assetContract,
            true,
            _receiver,
            _amount,
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
            "!amount"
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
        uint256 transferFee = (_amount * fee) / 10000;

        bytes32[] memory message = new bytes32[](3);
        message[0] = bytes32(uint256(uint160(_assetContract)));
        message[1] = _receiver;
        message[2] = bytes32(_amount - transferFee);

        fees[_assetContract] += transferFee * _mojoToTokenFactor;

        if (_transferAsset) {
            SafeERC20.safeTransferFrom(
                IERC20(_assetContract),
                msg.sender,
                address(this),
                _amount * _mojoToTokenFactor
            );
        }

        IPortal(portal).sendMessage{value: _messageFee}(
            bytes3("xch"), // chia
            chiaSideMintPuzzle,
            message
        );
    }

    function withdrawFees(
        address _assetContract,
        address[] memory _receivers,
        uint256[] memory _amounts
    ) public onlyOwner {
        require(_receivers.length == _amounts.length, "!length");

        for (uint256 i = 0; i < _receivers.length; i++) {
            fees[_assetContract] -= _amounts[i];

            SafeERC20.safeTransfer(
                IERC20(_assetContract),
                _receivers[i],
                _amounts[i]
            );
        }
    }

    function withdrawEtherFees(
        address[] memory _receivers,
        uint256[] memory _wethAmounts
    ) public onlyOwner {
        require(_receivers.length == _wethAmounts.length, "!length");

        uint256 wethAmount = 0;
        for (uint256 i = 0; i < _wethAmounts.length; i++) {
            fees[iweth] -= _wethAmounts[i];
            wethAmount += _wethAmounts[i];
        }

        IWETH(iweth).withdraw(wethAmount);

        for (uint256 i = 0; i < _receivers.length; i++) {
            payable(_receivers[i]).transfer(_wethAmounts[i] * wethToEthRatio);
        }
    }

    function rescueEther(uint256 amount) public onlyOwner {
        fees[iweth] += amount;

        IWETH(iweth).deposit{value: amount * wethToEthRatio}();
    }
}
