// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

interface IPortal {
    event MessageSent(
        bytes32 indexed nonce,
        bytes3 destination_chain,
        bytes32 destination,
        bytes32[] contents
    );

    function ethNonce() external returns (uint256);

    function messageToll() external returns (uint256);

    function sendMessage(
        bytes3 _destination_chain,
        bytes32 _destination,
        bytes32[] calldata _contents
    ) external payable;

    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes32 _source,
        address _destination,
        bytes32[] calldata _contents
    ) external;
}
