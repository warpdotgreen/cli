// SPDX-License-Identifier: MIT
/* ChatGPT tracks all over the place */
pragma solidity ^0.8.20;

import "../interfaces/IPortalMessageReceiver.sol";

contract PortalMessageReceiverMock is IPortalMessageReceiver {
    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] calldata _contents
    ) public override {
        // Do nothing
    }
}
