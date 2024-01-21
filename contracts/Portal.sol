// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IPortalMessageReceiver.sol";

contract Portal is Ownable {
    uint256 public ethNonce = 0;
    mapping(bytes32 => bool) private nonceUsed;

    event MessageSent(
        uint256 indexed nonce,
        bytes32 target,
        bool isPuzzleHash, // when false, target a singleton id
        uint256 deadline,
        bytes[] message
    );

    constructor() Ownable(msg.sender) {}

    function sendMessage(
        bytes32 _target,
        bool _isPuzzleHash,
        uint256 _deadline,
        bytes[] memory _message
    ) public {
        require(_deadline >= block.timestamp, "!deadline");
        ethNonce += 1;
        emit MessageSent(ethNonce, _target, _isPuzzleHash, _deadline, _message);
    }

    function receiveMessage(
        bytes32 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        address _target,
        uint256 _deadline,
        bytes memory _message
    ) public onlyOwner {
        require(!nonceUsed[_nonce], "!nonce");
        require(_deadline >= block.timestamp, "!deadline");

        nonceUsed[_nonce] = true;

        IPortalMessageReceiver(_target).receiveMessage(
            _nonce,
            _sender,
            _isPuzzleHash,
            _message
        );
    }
}
