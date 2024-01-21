// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "./interfaces/IPortalMessageReceiver.sol";
import "./interfaces/IPortal.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

interface ERC20Decimals {
    function decimals() external returns (uint8);
}

contract EthTokenBridge is IPortalMessageReceiver, Ownable {
    mapping(address => uint256) public fees;
    uint256 public fee = 100; // initial fee - 1%
    address public portal;
    bytes32 public chiaSideBurnPuzzle;
    bytes32 public chiaSideMintPuzzle;

    struct AssetReturnMessage {
        address assetContract;
        address receiver;
        uint256 amount;
    }

    constructor(
        address _portal,
        bytes32 _chiaSideBurnPuzzle,
        bytes32 _chiaSideMintPuzzle
    ) Ownable(msg.sender) {
        portal = _portal;
        chiaSideBurnPuzzle = _chiaSideBurnPuzzle;
        chiaSideMintPuzzle = _chiaSideMintPuzzle;
    }

    // fee between 0% and 5%
    function updateFee(uint256 _newFee) public onlyOwner {
        require(_newFee < 500, "fee too high");
        fee = _newFee;
    }

    function receiveMessage(
        bytes32 /* _nonce */,
        bytes32 _sender,
        bool _isPuzzleHash,
        bytes memory _message
    ) public {
        require(msg.sender == portal, "!portal");
        require(_sender == chiaSideBurnPuzzle && _isPuzzleHash, "!sender");

        AssetReturnMessage memory message = abi.decode(
            _message,
            (AssetReturnMessage)
        );

        uint256 chiaToEthFactor = 10 **
            ERC20Decimals(message.assetContract).decimals() /
            1000;
        message.amount = message.amount * chiaToEthFactor;
        uint256 transferFee = (message.amount * fee) / 10000;

        fees[message.assetContract] += transferFee;
        SafeERC20.safeTransfer(
            IERC20(message.assetContract),
            message.receiver,
            message.amount - transferFee
        );
    }

    function bridgeToChia(
        address _assetContract,
        bytes32 _receiver,
        uint256 _amount // on Chia
    ) public {
        uint256 transferFee = (_amount * fee) / 10000;

        bytes[] memory message = new bytes[](3);
        message[0] = abi.encode(_assetContract);
        message[1] = abi.encode(_receiver);
        message[2] = abi.encode(_amount - transferFee);

        uint256 chiaToEthFactor = 10 **
            ERC20Decimals(_assetContract).decimals() /
            1000;
        fees[_assetContract] += transferFee * chiaToEthFactor;
        SafeERC20.safeTransferFrom(
            IERC20(_assetContract),
            msg.sender,
            address(this),
            _amount * chiaToEthFactor
        );

        IPortal(portal).sendMessage(
            chiaSideMintPuzzle,
            true,
            block.timestamp + 10 * 12 * 365 days,
            message
        );
    }

    function withdrawFee(address _assetContract) public onlyOwner {
        uint256 amount = fees[_assetContract];
        fees[_assetContract] = 0;
        SafeERC20.safeTransfer(IERC20(_assetContract), msg.sender, amount);
    }
}
