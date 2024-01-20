// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IChiaBridgeMessageReceiver.sol";

contract Bridge is Ownable {
    uint256 public ethNonce = 0;
    mapping(uint256 => bool) private nonceUsed;

    event MessageSent(
        uint256 indexed nonce,
        bytes32 target,
        bool isPuzzleHash, // when false, target a singleton id
        uint256 deadline,
        bytes[] message
    );

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
        uint256 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        address _target,
        uint256 _deadline,
        bytes memory _message
    ) public onlyOwner {
        require(!nonceUsed[_nonce], "!nonce");
        require(_deadline <= block.timestamp, "!deadline");

        nonceUsed[_nonce] = true;

        (bool success, ) = IChiaBridgeMessageReceiver(_target).receiveMessage(
            _nonce,
            _sender,
            _isPuzzleHash,
            _message
        );
        require(success, "tx failed");
    }
}
