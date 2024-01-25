// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

interface IPortal {
    event MessageSent(
        bytes32 indexed nonce,
        bytes destination_chain,
        bytes destination_type,
        bytes destination_info,
        uint256 deadline,
        bytes[] contents
    );

    function ethNonce() external returns (uint256);

    function messageFee() external returns (uint256);

    function sendMessage(
        bytes memory _destination_chain,
        bytes memory _destination_type,
        bytes memory _destination_info,
        uint256 _deadline,
        bytes[] memory _contents
    ) external;

    function receiveMessage(
        bytes32 _nonce,
        bytes memory _source_chain,
        bytes memory _source_type,
        bytes memory _source_info,
        bytes memory _destination_chain,
        bytes memory _destination_type,
        bytes memory _destination_info,
        uint256 _deadline,
        bytes memory _contents
    ) external;
}
