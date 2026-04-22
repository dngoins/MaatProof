// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./MaatToken.sol";

/// @title Governance — On-chain DAO governance for the MaatProof protocol
/// @notice Allows $MAAT stakers to propose, vote on, and execute protocol changes.
///         Voting weight is quadratic: weight = sqrt(staked $MAAT) to dilute whale influence.
///         Successful proposals are subject to a TIMELOCK_DELAY before execution.
contract Governance {

    // ─── References ──────────────────────────────────────────────────────────

    MaatToken public immutable maatToken;

    // ─── Constants ───────────────────────────────────────────────────────────

    uint256 public constant VOTING_PERIOD      = 5 days;
    uint256 public constant TIMELOCK_DELAY     = 2 days;
    uint256 public constant QUORUM_PERCENT     = 10;   // 10% of total supply must vote
    uint256 public constant PASS_THRESHOLD     = 60;   // 60% of votes must be FOR
    uint256 public constant MIN_STAKE_TO_PROPOSE = 100_000e18; // 100k $MAAT

    // ─── Types ───────────────────────────────────────────────────────────────

    enum ProposalStatus {
        PENDING,
        VOTING,
        SUCCEEDED,
        DEFEATED,
        QUEUED,
        EXECUTED,
        CANCELLED
    }

    /// @dev Support values for vote(): 0 = against, 1 = for, 2 = abstain
    uint8 public constant VOTE_AGAINST = 0;
    uint8 public constant VOTE_FOR     = 1;
    uint8 public constant VOTE_ABSTAIN = 2;

    struct Proposal {
        uint256        id;
        string         title;
        bytes          callData;         // encoded function call to execute
        address        target;           // contract to call on execution
        address        proposer;
        uint256        forVotes;         // accumulated quadratic weight
        uint256        againstVotes;
        uint256        abstainVotes;
        ProposalStatus status;
        uint256        createdAt;
        uint256        votingEndsAt;
        uint256        timelockEndsAt;   // set when queued
        bool           executed;
    }

    // ─── Storage ─────────────────────────────────────────────────────────────

    uint256 public proposalCount;
    mapping(uint256 => Proposal)               public proposals;
    mapping(uint256 => mapping(address => bool)) public hasVoted;

    // ─── Events ──────────────────────────────────────────────────────────────

    event ProposalCreated(
        uint256 indexed proposalId,
        address indexed proposer,
        string  title,
        uint256 votingEndsAt
    );

    event VoteCast(
        uint256 indexed proposalId,
        address indexed voter,
        uint8   support,
        uint256 weight
    );

    event ProposalQueued(
        uint256 indexed proposalId,
        uint256 timelockEndsAt
    );

    event ProposalExecuted(uint256 indexed proposalId);

    event ProposalCancelled(uint256 indexed proposalId);

    // ─── Constructor ─────────────────────────────────────────────────────────

    /// @param _maatToken Address of the deployed MaatToken contract.
    constructor(address _maatToken) {
        require(_maatToken != address(0), "Zero address");
        maatToken = MaatToken(_maatToken);
    }

    // ─── Proposal Lifecycle ──────────────────────────────────────────────────

    /// @notice Submit a new governance proposal.
    /// @dev    Proposer must have at least MIN_STAKE_TO_PROPOSE staked $MAAT.
    /// @param  title    Short human-readable description of the proposal.
    /// @param  target   Contract address to call if proposal is executed.
    /// @param  callData ABI-encoded function call to invoke on `target`.
    /// @return proposalId The ID of the newly created proposal.
    function propose(
        string  calldata title,
        address          target,
        bytes   calldata callData
    ) external returns (uint256 proposalId) {
        require(
            maatToken.stakedBalanceOf(msg.sender) >= MIN_STAKE_TO_PROPOSE,
            "Insufficient stake to propose"
        );
        require(bytes(title).length > 0, "Title required");
        require(target != address(0),    "Target required");

        proposalCount++;
        proposalId = proposalCount;

        proposals[proposalId] = Proposal({
            id:             proposalId,
            title:          title,
            callData:       callData,
            target:         target,
            proposer:       msg.sender,
            forVotes:       0,
            againstVotes:   0,
            abstainVotes:   0,
            status:         ProposalStatus.VOTING,
            createdAt:      block.timestamp,
            votingEndsAt:   block.timestamp + VOTING_PERIOD,
            timelockEndsAt: 0,
            executed:       false
        });

        emit ProposalCreated(proposalId, msg.sender, title, block.timestamp + VOTING_PERIOD);
    }

    /// @notice Cast a vote on an active proposal.
    /// @dev    Vote weight = sqrt(staked $MAAT) using integer square root approximation.
    ///         Each address may vote exactly once per proposal.
    /// @param  proposalId The ID of the proposal to vote on.
    /// @param  support    0 = against, 1 = for, 2 = abstain.
    function vote(uint256 proposalId, uint8 support) external {
        Proposal storage p = proposals[proposalId];
        require(p.status == ProposalStatus.VOTING,          "Proposal not in voting");
        require(block.timestamp <= p.votingEndsAt,           "Voting period ended");
        require(!hasVoted[proposalId][msg.sender],           "Already voted");
        require(support <= VOTE_ABSTAIN,                     "Invalid support value");

        uint256 stakedAmount = maatToken.votingWeight(msg.sender);
        require(stakedAmount > 0, "No staked balance");

        uint256 weight = _sqrt(stakedAmount);
        hasVoted[proposalId][msg.sender] = true;

        if (support == VOTE_FOR) {
            p.forVotes += weight;
        } else if (support == VOTE_AGAINST) {
            p.againstVotes += weight;
        } else {
            p.abstainVotes += weight;
        }

        emit VoteCast(proposalId, msg.sender, support, weight);

        // Auto-finalize if voting period has ended (check on each vote as a convenience)
        _tryFinalize(proposalId);
    }

    /// @notice Queue a succeeded proposal for execution after the timelock delay.
    /// @dev    Anyone may call this once the voting period has ended and the proposal succeeded.
    /// @param  proposalId The ID of the proposal to queue.
    function queue(uint256 proposalId) external {
        _tryFinalize(proposalId);
        Proposal storage p = proposals[proposalId];
        require(p.status == ProposalStatus.SUCCEEDED, "Proposal did not succeed");

        p.status         = ProposalStatus.QUEUED;
        p.timelockEndsAt = block.timestamp + TIMELOCK_DELAY;

        emit ProposalQueued(proposalId, p.timelockEndsAt);
    }

    /// @notice Execute a queued proposal after the timelock delay has passed.
    /// @dev    Calls `target` with `callData` via a low-level call. Reverts if the call fails.
    /// @param  proposalId The ID of the proposal to execute.
    function execute(uint256 proposalId) external {
        Proposal storage p = proposals[proposalId];
        require(p.status == ProposalStatus.QUEUED,           "Proposal not queued");
        require(block.timestamp >= p.timelockEndsAt,          "Timelock not elapsed");
        require(!p.executed,                                  "Already executed");

        p.status   = ProposalStatus.EXECUTED;
        p.executed = true;

        (bool success, ) = p.target.call(p.callData);
        require(success, "Proposal execution failed");

        emit ProposalExecuted(proposalId);
    }

    /// @notice Cancel a proposal. Only the proposer or governance owner can cancel.
    /// @dev    Cannot cancel an already executed or defeated proposal.
    /// @param  proposalId The ID of the proposal to cancel.
    function cancel(uint256 proposalId) external {
        Proposal storage p = proposals[proposalId];
        require(
            msg.sender == p.proposer,
            "Only proposer can cancel"
        );
        require(
            p.status == ProposalStatus.VOTING ||
            p.status == ProposalStatus.SUCCEEDED ||
            p.status == ProposalStatus.QUEUED,
            "Cannot cancel in current state"
        );

        p.status = ProposalStatus.CANCELLED;
        emit ProposalCancelled(proposalId);
    }

    // ─── Views ───────────────────────────────────────────────────────────────

    /// @notice Returns whether a proposal has reached quorum.
    /// @param  proposalId The proposal ID to check.
    /// @return True if total votes cast meet the QUORUM_PERCENT threshold.
    function hasReachedQuorum(uint256 proposalId) public view returns (bool) {
        Proposal storage p = proposals[proposalId];
        uint256 totalVotes  = p.forVotes + p.againstVotes + p.abstainVotes;
        uint256 totalSupply = maatToken.totalSupply();
        // Compare raw staked amounts; quorum based on sqrt sums approximation
        return totalVotes * 100 >= _sqrt(totalSupply) * QUORUM_PERCENT;
    }

    /// @notice Returns whether a proposal has passed the pass threshold.
    /// @param  proposalId The proposal ID to check.
    /// @return True if FOR votes exceed PASS_THRESHOLD % of FOR + AGAINST votes.
    function hasPassed(uint256 proposalId) public view returns (bool) {
        Proposal storage p = proposals[proposalId];
        uint256 decisive = p.forVotes + p.againstVotes;
        if (decisive == 0) return false;
        return p.forVotes * 100 >= decisive * PASS_THRESHOLD;
    }

    // ─── Internal ────────────────────────────────────────────────────────────

    function _tryFinalize(uint256 proposalId) internal {
        Proposal storage p = proposals[proposalId];
        if (p.status != ProposalStatus.VOTING) return;
        if (block.timestamp <= p.votingEndsAt)  return;

        if (hasReachedQuorum(proposalId) && hasPassed(proposalId)) {
            p.status = ProposalStatus.SUCCEEDED;
        } else {
            p.status = ProposalStatus.DEFEATED;
        }
    }

    /// @dev Integer square root (Babylonian method) for quadratic voting weight.
    function _sqrt(uint256 x) internal pure returns (uint256 y) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }
}
