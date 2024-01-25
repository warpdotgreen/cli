// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

interface IPortalMessageReceiver {
    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes1 _source_type,
        bytes32 _source_info,
        bytes memory _contents
    ) external;
}
