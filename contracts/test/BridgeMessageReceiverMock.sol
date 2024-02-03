// SPDX-License-Identifier: MIT
/* ChatGPT tracks all over the place */
pragma solidity ^0.8.20;

import "../interfaces/IPortalMessageReceiver.sol";

contract PortalMessageReceiverMock is IPortalMessageReceiver {
    // Event for logging
    event MessageReceived(
        bytes32 nonce,
        bytes3 source_chain,
        bytes32 source,
        bytes32[] contents
    );

    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] memory _contents
    ) public override {
        // Emit an event for testing purposes
        emit MessageReceived(_nonce, _source_chain, _source, _contents);

        // Additional logic can be added here if needed for more complex tests
    }
}
