// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

interface IPortalMessageReceiver {
    function receiveMessage(
        bytes32 _nonce,
        bytes memory _source_chain,
        bytes memory _source_type,
        bytes memory _source_info,
        bytes memory _contents
    ) external;
}
