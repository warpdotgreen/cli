// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

interface IPortalMessageReceiver {
    function receiveMessage(
        bytes32 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        bytes memory _message
    ) external;
}
