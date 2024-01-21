// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "./interfaces/IChiaBridgeMessageReceiver.sol";
import "@openzeppelin/contracts/erc20/IERC20.sol";
import "@openzeppelin/contracts/acess/Ownable.sol";

// owner can only withdraw fees
contract EthTokenMaster is IChiaBridgeMessageReceiver, Ownable {
    mapping(address => uint256) fees;
    uint256 fee = 100; // initial fee - 1%
    address private bridge;
    bytes32 private chiaSideBurnPuzzle;
    bytes32 private chiaSideMintPuzzle;

    struct AssetReturnMessage {
        address assetContract;
        address receiver;
        uint256 amount;
    }

    constructor(
        address _bridge,
        bytes32 _chiaSideBurnPuzzle,
        bytes32 _chiaSideMintPuzzle
    ) {
        bridge = _bridge;
        chiaSideBurnPuzzle = _chiaSideBurnPuzzle;
        chiaSideMintPuzzle = _chiaSideMintPuzzle;
    }

    // fee between 0% and 5%
    function updateFee(uint256 _newFee) public onlyOwner {
        require(_newFee < 500, "fee too high");
        fee = _newFee;
    }

    function receiveMessage(
        uint256 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        bytes _message
    ) public {
        require(msg.sender == bridge, "!bridge");
        require(_sender == chiaSideBurnPuzzle && _isPuzzleHash, "!sender");

        AssetReturnMessage memory message = abi.decode(
            _message,
            (AssetReturnMessage)
        );

        uint256 fee = (message.amount * fee) / 10000;
        fees[message.assetContract] += fee;
        IERC20(message.assetContract).safeTransfer(
            message.receiver,
            message.amount - fee
        );
    }

    function bridgeToChia(
        address _assetContract,
        bytes32 _receiver,
        uint256 _amount // on Chia
    ) public {
        uint256 fee = (message.amount * fee) / 10000;

        bytes[] memory message = new bytes[](3);
        message[0] = abi.encode(_assetContract);
        message[1] = abi.encode(_receiver);
        message[2] = abi.encode(_amount - fee);

        fees[_assetContract] += fee * 1e9;
        IERC20(_assetContract).safeTransferFrom(
            msg.sender,
            address(this),
            _amount * 1e9
        );

        Bridge(bridge).sendMessage(
            chiaSideMintPuzzle,
            true,
            block.timestamp + 10 years,
            message
        );
    }

    function withdrawFee(address _assetContract) public onlyOwner {
        uint256 amount = fees[_assetContract];
        fees[_assetContract] = 0;
        IERC20(_assetContract).safeTransfer(msg.sender, amount);
    }
}
