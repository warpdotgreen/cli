// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IPortalMessageReceiver.sol";

contract Portal is Ownable {
    uint256 public ethNonce = 0;
    mapping(bytes32 => bool) private nonceUsed;
    address private feeOwner;
    uint256 public messageFee;

    event MessageSent(
        bytes32 indexed nonce,
        bytes3 destination_chain,
        bytes1 destination_type,
        bytes32 destination_info,
        uint256 deadline,
        bytes[] contents
    );

    constructor(
        address _messageMultisig,
        address _feeOwner,
        uint256 _messageFee
    ) Ownable(_messageMultisig) {
        feeOwner = _feeOwner;
        messageFee = _messageFee;
    }

    receive() external payable {}

    function sendMessage(
        bytes3 _destination_chain,
        bytes1 _destination_type,
        bytes32 _destination_info,
        uint256 _deadline,
        bytes[] memory _contents
    ) public payable {
        require(_deadline >= block.timestamp, "!deadline");
        require(msg.value == messageFee, "!fee");
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
        bytes3 _source_chain,
        bytes1 _source_type,
        bytes32 _source_info,
        address _destination_info,
        uint256 _deadline,
        bytes memory _contents
    ) public onlyOwner {
        require(!nonceUsed[_nonce], "!nonce");
        require(_deadline >= block.timestamp, "!deadline");

        nonceUsed[_nonce] = true;

        IPortalMessageReceiver(address(_destination_info)).receiveMessage(
            _nonce,
            _source_chain,
            _source_type,
            _source_info,
            _contents
        );
    }

    function withdrawFees(
        address[] memory _receivers,
        uint256[] memory _amounts
    ) public {
        require(msg.sender == feeOwner, "!feeOwner");

        for (uint256 i = 0; i < _receivers.length; i++) {
            payable(_receivers[i]).transfer(_amounts[i]);
        }
    }
}
