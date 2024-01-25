// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IPortalMessageReceiver.sol";

contract Portal is Ownable {
    uint256 public ethNonce = 0;
    mapping(bytes32 => bool) private nonceUsed;

    event MessageSent(
        bytes32 indexed nonce,
        bytes destination_chain,
        bytes destination_type,
        bytes destination_info,
        uint256 deadline,
        bytes[] contents
    );

    constructor() Ownable(msg.sender) {}

    function sendMessage(
        bytes memory _destination_chain,
        bytes memory _destination_type,
        bytes memory _destination_info,
        uint256 _deadline,
        bytes[] memory _contents
    ) public {
        require(_deadline >= block.timestamp, "!deadline");
        ethNonce += 1;
        emit MessageSent(
            bytes32(ethNonce),
            _destination_chain,
            _destination_type,
            _destination_info,
            _deadline,
            _contents
        );
    }

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
    ) public onlyOwner {
        require(!nonceUsed[_nonce], "!nonce");
        require(_deadline >= block.timestamp, "!deadline");

        nonceUsed[_nonce] = true;

        IPortalMessageReceiver(_target).receiveMessage(
            _nonce,
            _source_chain,
            _source_type,
            _source_info,
            _deadline,
            _contents
        );
    }
}
