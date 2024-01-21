// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

interface IBridge {
    event MessageSent(
        uint256 indexed nonce,
        bytes32 target,
        bool isPuzzleHash, // when false, target is a singleton id
        uint256 deadline,
        bytes[] message
    );

    function ethNonce() external returns (uint256);

    function sendMessage(
        bytes32 _target,
        bool _isPuzzleHash,
        uint256 _deadline,
        bytes[] memory message
    ) external;

    function receiveMessage(
        uint256 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        address _target,
        uint256 _deadline,
        bytes memory _message
    ) external;
}
