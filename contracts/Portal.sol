// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity 0.8.23;

import "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import "@openzeppelin/contracts-upgradeable/access/OwnableUpgradeable.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "./interfaces/IPortalMessageReceiver.sol";

/**
 * @title   warp.green Portal Contract
 * @notice  Manages the sending and receiving of cross-chain messages via a trusted set of validators.
 * @dev     Sits behind a TransparentUpgradeableProxy. Owner is a cold key multisig (Safe{Wallet}) controlled by a majority of the trusted validators.
 */
contract Portal is Initializable, OwnableUpgradeable {
    /**
     * @dev  Tracks the incremental nonce for Ethereum-originating messages.
     */
    uint256 public ethNonce = 0;

    /**
     * @dev  Tracks which messages have been relayd to ensure that no message is relayed more than once. Key is the keccak256 hash of the source chain and nonce.
     */
    mapping(bytes32 => bool) private usedNonces;

    /**
     * @notice  The 'fee' required to send a message via the portal. The toll is sent to the block's miner in the same transaction - it is not kept by the protocol.
     * @dev     The value is sent to block.coinbase.
     */
    uint256 public messageToll;

    /**
     * @notice  Indicates if an address is authorized as a signer (validator) for message verification.
     */
    mapping(address => bool) public isSigner;

    /**
     * @notice  The number of signatures required to relay a message.
     */
    uint256 public signatureThreshold;

    /**
     * @notice  Logs when a message is successfully sent.
     * @dev     Source chain is the current chain's ID.
     * @param   nonce Unique identifier for the message on this chain.
     * @param   source Address or puzzle hash of the message sender.
     * @param   destination_chain Target chain ID.
     * @param   destination Target (e.g., puzzle hash) on the destination chain.
     * @param   contents Message content or payload.
     */
    event MessageSent(
        bytes32 indexed nonce,
        address source,
        bytes3 destination_chain,
        bytes32 destination,
        bytes32[] contents
    );

    /**
     * @notice  Logs when a message is successfully relayed to its target on this chain.
     * @dev     Destination chain is the current chain's ID.
     * @param   nonce Unique identifier for the message on this chain.
     * @param   source_chain Source chain ID.
     * @param   source Address or puzzle hash on the source chain that sent the message.
     * @param   destination Address of the contract handling the message on the current chain.
     * @param   contents Message content or payload.
     */
    event MessageReceived(
        bytes32 indexed nonce,
        bytes3 source_chain,
        bytes32 source,
        address destination,
        bytes32[] contents
    );

    /**
     * @notice  Indicates an update to the signer list.
     * @param   signer The address of the signer.
     * @param   isSigner Whether the address is authorized as a signer.
     */
    event SignerUpdated(address signer, bool isSigner);

    /**
     * @notice  Indicates a change in the required number of signatures.
     * @param   newThreshold New signature count required for message verification.
     */
    event SignagtureThresholdUpdated(uint256 newThreshold);

    /**
     * @notice  Indicates a change in the message toll.
     * @param   newFee New toll required to send a message.
     */
    event MessageTollUpdated(uint256 newFee);

    /**
     * @notice  Initializes the contract with signers and settings for message handling.
     * @dev     Sets initial owners, message toll, signers, and signature threshold. Can only be called once.
     * @param   _coldMultisig The address that will own the contract.
     * @param   _messageToll Initial toll required to send messages.
     * @param   _signers Initial set of addresses authorized to attest messages were sent to this chain.
     * @param   _signatureThreshold Number of required signatures to validate a message.
     */
    function initialize(
        address _coldMultisig,
        uint256 _messageToll,
        address[] calldata _signers,
        uint256 _signatureThreshold
    ) external initializer {
        __Ownable_init(_coldMultisig);

        messageToll = _messageToll;
        signatureThreshold = _signatureThreshold;

        for (uint256 i = 0; i < _signers.length; i++) {
            isSigner[_signers[i]] = true;
        }
    }

    /**
     * @notice  Allows the contract to be sent ether.
     * @dev     No checks to prevent user error. Highly unlikely that any dApp will send ether this way by mistake.
     */
    receive() external payable {}

    /**
     * @notice  Sends a cross-chain message to another blockchain.
     * @dev     Charges a toll, sends it to the miner, increments the nonce, and emits a MessageSent event. Source is `msg.sender`.
     * @param   _destination_chain The target blockchain's chain ID.
     * @param   _destination The target address or puzzle hash on the destination chain.
     * @param   _contents The content of the message being sent.
     */
    function sendMessage(
        bytes3 _destination_chain,
        bytes32 _destination,
        bytes32[] calldata _contents
    ) external payable {
        require(msg.value == messageToll, "!toll");
        ethNonce += 1;

        (bool success, ) = block.coinbase.call{value: msg.value}(new bytes(0));
        require(success, "!toll");

        emit MessageSent(
            bytes32(ethNonce),
            msg.sender,
            _destination_chain,
            _destination,
            _contents
        );
    }

    /**
     * @notice  Receives and relays a cross-chain message from another blockchain.
     * @dev     Verifies the message signatures, checks for replay, and if valid, forwards the message to the destination contract. Normally called by the user that sent the message
     * @param   _nonce The source-chain unique identifier of the message.
     * @param   _source_chain The chain ID from which the message originates.
     * @param   _source The source address or puzzle hash on the source chain.
     * @param   _destination The contract address that will receive and process the message on this chain.
     * @param   _contents The content of the message being processed.
     * @param   _sigs The signatures verifying the message.
     */
    function receiveMessage(
        bytes32 _nonce,
        bytes3 _source_chain,
        bytes32 _source,
        address _destination,
        bytes32[] calldata _contents,
        bytes memory _sigs
    ) external {
        require(_sigs.length == signatureThreshold * 65, "!len");

        bytes32 messageHash = keccak256(
            abi.encodePacked(
                "\x19Ethereum Signed Message:\n32",
                keccak256(
                    abi.encodePacked(
                        _nonce,
                        _source_chain,
                        _source,
                        _destination,
                        _contents
                    )
                )
            )
        );

        address lastSigner = address(0);

        for (uint256 i = 0; i < signatureThreshold; i++) {
            uint8 v;
            bytes32 r;
            bytes32 s;

            assembly {
                let ib := add(mul(65, i), 32)
                v := byte(0, mload(add(_sigs, ib)))
                r := mload(add(_sigs, add(1, ib)))
                s := mload(add(_sigs, add(33, ib)))
            }

            address signer = ecrecover(messageHash, v, r, s);
            require(isSigner[signer], "!signer");
            require(signer > lastSigner, "!order");
            lastSigner = signer;
        }

        bytes32 key = keccak256(abi.encodePacked(_source_chain, _nonce));
        require(!usedNonces[key], "!nonce");
        usedNonces[key] = true;

        IPortalMessageReceiver(_destination).receiveMessage(
            _nonce,
            _source_chain,
            _source,
            _contents
        );

        emit MessageReceived(
            _nonce,
            _source_chain,
            _source,
            _destination,
            _contents
        );
    }

    /**
     * @notice  Allows the validators to transfer ether owned by this contract to a list of addresses.
     * @dev     Only callable by the contract owner (validator cold key multisig).
     * @param   _receivers Array of addresses to receive the ether.
     * @param   _amounts Corresponding amounts of ether to be sent to the receivers.
     */
    function rescueEther(
        address[] calldata _receivers,
        uint256[] calldata _amounts
    ) external onlyOwner {
        for (uint256 i = 0; i < _receivers.length; i++) {
            payable(_receivers[i]).transfer(_amounts[i]);
        }
    }

    /**
     * @notice  Allows the validators to transfer ERC-20 owned by the contract to a list of addresses.
     * @dev     Only callable by the contract owner (validator cold key multisig).
     * @param   _assetContract Address of the ERC-20 token to transfer.
     * @param   _receivers Array of addresses to receive the tokens.
     * @param   _amounts Corresponding amounts of tokens to be sent to the receivers.
     */
    function rescueAsset(
        address _assetContract,
        address[] calldata _receivers,
        uint256[] calldata _amounts
    ) external onlyOwner {
        for (uint256 i = 0; i < _receivers.length; i++) {
            SafeERC20.safeTransfer(
                IERC20(_assetContract),
                _receivers[i],
                _amounts[i]
            );
        }
    }

    /**
     * @notice  Updates the authorization status of a signer (validator).
     * @dev     Only callable by the contract owner (validator cold key multisig).
     * @param   _signer Address of the signer to update.
     * @param   _newValue New authorization status (true for authorized, false for not authorized).
     */
    function updateSigner(address _signer, bool _newValue) external onlyOwner {
        require(isSigner[_signer] != _newValue, "!diff");
        isSigner[_signer] = _newValue;

        emit SignerUpdated(_signer, _newValue);
    }

    /**
     * @notice  Updates the threshold of required signatures for message verification.
     * @dev     Only callable by the contract owner (validator cold key multisig).
     * @param   _newValue New number of required signatures.
     */
    function updateSignatureThreshold(uint256 _newValue) external onlyOwner {
        require(signatureThreshold != _newValue && _newValue > 0, "!val");
        signatureThreshold = _newValue;

        emit SignagtureThresholdUpdated(_newValue);
    }

    /**
     * @notice  Updates the message toll fee required to send messages.
     * @dev     Only callable by the contract owner (validator cold key multisig).
     * @param   _newValue New toll fee.
     */
    function updateMessageToll(uint256 _newValue) external onlyOwner {
        require(messageToll != _newValue, "!diff");
        messageToll = _newValue;

        emit MessageTollUpdated(_newValue);
    }
}
