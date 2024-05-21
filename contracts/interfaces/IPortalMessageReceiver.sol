// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity 0.8.23;

interface IPortalMessageReceiver {
    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] calldata _contents
    ) external;
}
