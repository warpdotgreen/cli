// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/IOwnable.sol";

contract ChiaBridge is IOwnable {
    event MessageSent(
        uint256 indexed nonce,
        bytes32 target,
        bool isPuzzleHash, // when false, target is a singleton id
        uint256 deadline,
        bytes[] message
    );

    function ethNonce() public unit256;

    function sendMessage(
        bytes32 _target,
        bool _isPuzzleHash,
        uint256 _deadline,
        bytes[] message
    ) public;

    function receiveMessage(
        uint256 _nonce,
        bytes32 _sender,
        address _target,
        uint256 _deadline,
        bytes _message
    ) public;
}
