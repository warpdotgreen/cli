// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity 0.8.23;

import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "./interfaces/IPortalMessageReceiver.sol";
import "./interfaces/IPortal.sol";

/**
 * @title   Wrapped CAT Token Contract
 * @notice  ERC-20 representing wrapped Chia Asset Tokens by leveraging the warp.green protocol.
 * @dev     This contract manages the wrapping and unwrapping of Chia Asset Tokens (CATs) on Ethereum, allowing CATs to be used within the Ethereum ecosystem.
 */
contract WrappedCAT is ERC20, ERC20Permit, IPortalMessageReceiver {
    /**
     * @notice  The address of the warp.green portal contract.
     * @dev     Used as an oracle. The portal usually sits behind a TransparentUpgradeableProxy.
     */
    address public immutable portal;

    /**
     * @notice  When wrapping or unwrapping CATs, a tip is sent to the warp.green protocol. The tip is expressed in basis points.
     * @dev     Tip is calculated as amount * tip / 10000 - the remaining amount is sent to the recipient.
     */
    uint16 public immutable tip;

    /**
     * @notice  Ratio used to convert mojos (smallest unit on Chia) to token units on Ethereum.
     * @dev     Token amount on this chain = [mojos on Chia] * `mojoToTokenRatio`
     */
    uint64 public immutable mojoToTokenRatio;

    /**
     * @notice  The chain id of the original CATs. Usually "xch", which indicates Chia.
     * @dev     Used to verify the source chain of messages.
     */
    bytes3 public immutable otherChain;

    /**
     * @notice  The hash of the puzzle used to verifiably lock CATs on Chia. Sender of messages from Chia.
     * @dev     Used to verify the source of messages when they're received.
     */
    bytes32 public lockerPuzzleHash;

    /**
     * @notice  The hash of the puzzle used to unlock CATs on Chia. Message receiver on Chia.
     * @dev     Used as a destination for sent messages after ERC-20 tokens are burned.
     */
    bytes32 public unlockerPuzzleHash;

    /**
     * @notice  Creates a Wrapped CAT token linked to a specific Chia Asset Token.
     * @dev     ERC-20 has 18 decimals - `mojoToTokenRatio` should be set accordingly.
     * @param   _name Name of the token.
     * @param   _symbol Symbol of the token.
     * @param   _portal Address of the warp.green portal contract.
     * @param   _tip Tip percentage (in basis points) paid to the portal.
     * @param   _mojoToTokenRatio Conversion ratio from mojos to token units.
     * @param   _otherChain ID of chain where CATs are locked (e.g., Chia).
     */
    constructor(
        string memory _name,
        string memory _symbol,
        address _portal,
        uint16 _tip,
        uint64 _mojoToTokenRatio,
        bytes3 _otherChain
    ) ERC20(_name, _symbol) ERC20Permit(_name) {
        portal = _portal;
        tip = _tip;
        mojoToTokenRatio = _mojoToTokenRatio;
        otherChain = _otherChain;
    }

    /**
     * @notice  Initializes puzzle hashes for locking and unlocking tokens. Should be called in the same transaction as deployment.
     * @dev     Allows the address of the contract to be determined using CREATE2, as the arguments below depend on the address of this contract. Can only be called once per contract lifetime.
     * @param   _lockerPuzzleHash Puzzle hash for locking CATs on Chia.
     * @param   _unlockerPuzzleHash Puzzle hash for unlocking CATs on Chia.
     */
    function initializePuzzleHashes(
        bytes32 _lockerPuzzleHash,
        bytes32 _unlockerPuzzleHash
    ) public {
        require(
            lockerPuzzleHash == bytes32(0) && unlockerPuzzleHash == bytes32(0),
            "nope"
        );

        lockerPuzzleHash = _lockerPuzzleHash;
        unlockerPuzzleHash = _unlockerPuzzleHash;
    }

    /**
     * @notice  Receives and processes messages from the warp.green portal
     * @dev     Uses the warp.green portal contract as an oracle; verifies message and handles the unwrapping process.
     * @param   _source_chain Message source chain ID (e.g., "xch").
     * @param   _source Message source (puzzle hash). Must match the locker puzzle hash.
     * @param   _contents Message contents - receiver address and mojo amount.
     */
    function receiveMessage(
        bytes32 /* _nonce */,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] calldata _contents
    ) public {
        require(
            msg.sender == portal &&
                _source == lockerPuzzleHash &&
                _source_chain == otherChain,
            "!msg"
        );

        uint256 amount = uint256(_contents[1]) * mojoToTokenRatio;
        uint256 transferTip = (amount * tip) / 10000;

        _mint(address(uint160(uint256(_contents[0]))), amount - transferTip);
        _mint(portal, transferTip);
    }

    /**
     * @notice  Burns Wrapped CAT ERC-20s and sends a message to unlock the original CAT tokens on Chia.
     * @dev     Verifies the toll payment, burns ERC-20s, and sends a message to unlock CATs.
     * @param   _receiver Puzzle hash of the receiver on Chia.
     * @param   _mojoAmount Amount of CAT tokens (in mojos) to unlock on Chia.
     */
    function bridgeBack(bytes32 _receiver, uint256 _mojoAmount) public payable {
        require(msg.value == IPortal(portal).messageToll(), "!toll");

        uint256 transferTip = (_mojoAmount * tip) / 10000;
        _burn(msg.sender, _mojoAmount * mojoToTokenRatio);
        _mint(portal, transferTip * mojoToTokenRatio);

        bytes32[] memory contents = new bytes32[](2);
        contents[0] = _receiver;
        contents[1] = bytes32(_mojoAmount - transferTip);

        IPortal(portal).sendMessage{value: msg.value}(
            otherChain,
            unlockerPuzzleHash,
            contents
        );
    }
}
