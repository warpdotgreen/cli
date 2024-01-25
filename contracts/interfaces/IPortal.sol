// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

interface IPortal {
    event MessageSent(
        bytes32 indexed nonce,
        bytes3 destination_chain,
        bytes1 destination_type,
        bytes32 destination_info,
        uint256 deadline,
        bytes[] contents
    );

    function ethNonce() external returns (uint256);

    function messageFee() external returns (uint256);

    function sendMessage(
        bytes3 _destination_chain,
        bytes1 _destination_type,
        bytes32 _destination_info,
        uint256 _deadline,
        bytes[] memory _contents
    ) external payable;

    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes1 _source_type,
        bytes32 _source_info,
        address _destination_info,
        uint256 _deadline,
        bytes memory _contents
    ) external;
}
