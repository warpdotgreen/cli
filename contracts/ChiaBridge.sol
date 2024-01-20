// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IChiaBridgeMessageReceiver.sol";

contract ChiaBridge is Ownable {
    uint256 public ethNonce = 0;
    mapping(uint256 => bool) private nonceUsed;

    event MessageSent(
        uint256 indexed nonce,
        bytes32 target,
        bool isPuzzleHash, // when false, target a singleton id
        bytes[] message
    );

    function sendMessage(
        bytes32 _target,
        bool _isPuzzleHash,
        bytes[] memory _message
    ) public {
        ethNonce += 1;
        emit MessageSent(ethNonce, _target, _isPuzzleHash, _message);
    }

    function receiveMessage(
        uint256 _nonce,
        bytes32 _sender,
        address _target,
        bytes memory _message
    ) public onlyOwner {
        require(!nonceUsed[_nonce], "tx nonce already used");
        nonceUsed[_nonce] = true;

        (bool success, ) = IChiaBridgeMessageReceiver(_target).receiveMessage(
            _nonce,
            _sender,
            _message
        );
        require(success, "tx failed");
    }
}
