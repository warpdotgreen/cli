// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "./interfaces/IChiaBridgeMessageReceiver.sol";
import "@openzeppelin/contracts/erc20/IERC20.sol";

contract EthTokenMaster is IChiaBridgeMessageReceiver {
    address private bridge;
    bytes32 private chiaBridgeSenderSingleton;
    bytes32 private chiaBridgeReceiverSingleton;

    struct AssetReturnMessage {
        address assetContract;
        address receiver;
        uint256 amount;
    }

    constructor(
        address _bridge,
        bytes32 _chiaBridgeSenderSingleton,
        bytes32 _chiaBridgeReceiverSingleton
    ) {
        bridge = _bridge;
        chiaBridgeSenderSingleton = _chiaBridgeSenderSingleton;
        chiaBridgeReceiverSingleton = _chiaBridgeReceiverSingleton;
    }

    function receiveMessage(
        uint256 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        bytes _message
    ) public {
        require(msg.sender == bridge, "!bridge");
        require(
            _sender == chiaBridgeSenderSingleton && !_isPuzzleHash,
            "!sender"
        );

        AssetReturnMessage memory message = abi.decode(
            _message,
            (AssetReturnMessage)
        );

        IERC20(message.assetContract).safeTransfer(
            message.receiver,
            message.amount
        );
    }

    function bridgeToChia(
        address _assetContract,
        bytes32 _receiver,
        uint256 _amount // on Chia
    ) public {
        bytes[] memory message = new bytes[](3);
        message[0] = abi.encode(_assetContract);
        message[1] = abi.encode(_receiver);
        message[2] = abi.encode(_amount);

        IERC20(_assetContract).safeTransferFrom(
            msg.sender,
            address(this),
            _amount * 1e9
        );
        Bridge(bridge).sendMessage(
            chiaBridgeReceiverSingleton,
            false,
            block.timestamp + 10 years,
            message
        );
    }
}
