// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IPortalMessageReceiver.sol";

contract Portal is Ownable {
    uint256 public ethNonce = 0;
    mapping(bytes32 => bool) private nonceUsed;
    address public feeCollector;
    uint256 public messageFee;

    event MessageSent(
        bytes32 indexed nonce,
        bytes3 destination_chain,
        bytes32 destination,
        bytes32[] contents
    );

    constructor(
        address _messageMultisig,
        address _feeCollector,
        uint256 _messageFee
    ) Ownable(_messageMultisig) {
        feeCollector = _feeCollector;
        messageFee = _messageFee;
    }

    receive() external payable {}

    function sendMessage(
        bytes3 _destination_chain,
        bytes32 _destination,
        bytes32[] memory _contents
    ) public payable {
        require(msg.value == messageFee, "!fee");
        ethNonce += 1;
        emit MessageSent(
            bytes32(ethNonce),
            _destination_chain,
            _destination,
            _contents
        );
    }

    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes32 _source,
        address _destination,
        bytes32[] memory _contents
    ) public onlyOwner {
        require(!nonceUsed[_nonce], "!nonce");

        nonceUsed[_nonce] = true;

        IPortalMessageReceiver(_destination).receiveMessage(
            _nonce,
            _source_chain,
            _source,
            _contents
        );
    }

    function withdrawFees(
        address[] memory _receivers,
        uint256[] memory _amounts
    ) public {
        require(msg.sender == feeCollector, "!feeCollector");

        for (uint256 i = 0; i < _receivers.length; i++) {
            payable(_receivers[i]).transfer(_amounts[i]);
        }
    }
}
