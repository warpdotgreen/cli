// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "./interfaces/IChiaBridgeMessageReceiver.sol";

contract ChiaTokenMaster is IChiaBridgeMessageReceiver {
    mapping(bytes32 => address) public wrappedTokens;
    address private bridge;
    bytes32 private chiaBridgeSenderSingleton;
    bytes32 private chiaBridgeReceiverSingleton;

    struct MintMessage {
        bytes32 assetId;
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
            _sender == chiaBridgeSenderSingleton && _isPuzzleHash,
            "!sender"
        );

        MintMessage memory message = abi.decode(_message, (MintMessage));

        address wrappedToken = wrappedTokens[message.assetId];
        if (wrappedToken == address(0)) {
            if (message.assetId == bytes32(0)) {
                wrappedToken = address(
                    new WrappedToken("Wrapped Chia", "wXCH")
                );
            } else {
                wrappedToken = address(
                    new WrappedToken("Chia Wrapped Asset", "CWA")
                );
            }
            wrappedAssets[message.assetId] = wrappedAsset;
        }

        if (message.assetId == bytes32(0)) {
            // XCH has 12 decimals
            WrappedToken(wrappedToken).mint(
                message.receiver,
                message.amount * 1e6
            );
        } else {
            // Other asset - 3 decimals
            WrappedToken(wrappedToken).mint(
                message.receiver,
                message.amount * 1e15
            );
        }
    }

    function bridgeToChia(
        bytes32 _assetId,
        bytes32 _receiver,
        uint256 _amount // on Chia
    ) public {
        bytes[] memory message = new bytes[](3);
        message[0] = abi.encode(_assetId);
        message[1] = abi.encode(_receiver);
        message[2] = abi.encode(_amount);

        WrappedToken wrappedToken = wrappedTokens[_assetId];
        require(wrappedToken != address(0), "!wrappedToken");

        if (_assetId == bytes32(0)) {
            _amount = _amount * 1e6;
        } else {
            _amount = _amount * 1e15;
        }
        WrappedToken(wrappedToken).burn(msg.sender, _amount);
        ChiaBridge(bridge).sendMessage(
            chiaBridgeReceiverSingleton,
            false,
            message
        );
    }
}
