// SPDX-License-Identifier: MIT
/* ChatGPT tracks all over the place */
pragma solidity ^0.8.20;

import "../interfaces/IPortalMessageReceiver.sol";

contract PortalMessageReceiverMock is IPortalMessageReceiver {
    // Event for logging
    event MessageReceived(
        uint256 nonce,
        bytes32 sender,
        bool isPuzzleHash,
        bytes message
    );

    function receiveMessage(
        uint256 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        bytes memory _message
    ) public override {
        // Emit an event for testing purposes
        emit MessageReceived(_nonce, _sender, _isPuzzleHash, _message);

        // Additional logic can be added here if needed for more complex tests
    }
}
