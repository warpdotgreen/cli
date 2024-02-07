// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import "./interfaces/IPortalMessageReceiver.sol";

contract Portal is Initializable, OwnableUpgradeable {
    uint256 public ethNonce = 0;
    mapping(bytes32 => bool) private nonceUsed;

    uint256 public messageFee;

    mapping(address => bool) public isSigner;
    uint256 public signatureThreshold;

    event MessageSent(
        bytes32 indexed nonce,
        bytes3 destination_chain,
        bytes32 destination,
        bytes32[] contents
    );

    event SignerUpdated(
        address signer,
        bool isSigner
    );

    event ThresholdUpdated(
        uint256 newThreshold
    );

    function initialize(
        address _coldMultisig,
        uint256 _messageFee,
        address[] memory _signers,
        uint256 _signatureThreshold
    ) public initializer {
        __Ownable_init(_coldMultisig);

        messageFee = _messageFee;
        signatureThreshold = _signatureThreshold;

        for (uint256 i = 0; i < _signers.length; i++) {
            isSigner[_signers[i]] = true;
        }
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
        bytes32[] memory _contents,
        uint8[] memory _v,
        bytes32[] memory _r,
        bytes32[] memory _s
    ) public {
        require(
            _v.length == _r.length &&
            _s.length == _r.length &&
            _r.length == signatureThreshold,
            "!len"
        );

        bytes32 messageHash = keccak256(
            abi.encodePacked(
                _nonce,
                _source_chain,
                _source,
                _destination,
                _contents
            )
        );
        address lastSigner = address(0);

        for(uint256 i = 0; i < signatureThreshold; i++) {
            address signer = ecrecover(
                messageHash,
                _v[i],
                _r[i],
                _s[i]
            );
            require(isSigner[signer], "!signer");
            require(signer > lastSigner, "!order");
            lastSigner = signer;
        }
        
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
    ) public onlyOwner {
        for (uint256 i = 0; i < _receivers.length; i++) {
            payable(_receivers[i]).transfer(_amounts[i]);
        }
    }

    function updateSigner(address _signer, bool _newValue) public onlyOwner {
        isSigner[_signer] = _newValue;

        emit SignerUpdated(
            _signer,
            _newValue
        );
    }

    function updateSignatureThreshold(uint256 _newValue) public onlyOwner {
        signatureThreshold = _newValue;

        emit ThresholdUpdated(
            _newValue
        );
    }
}
