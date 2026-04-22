// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./MaatToken.sol";

/// @title Slashing — MaatProof slashing contract
/// @notice Handles evidence submission, governance vote, slash execution,
///         fund distribution, and appeals for protocol violations.
contract Slashing {

    // ─── Types ───────────────────────────────────────────────────────────────

    enum SlashCondition {
        VAL_DOUBLE_VOTE,          // 100% slash
        VAL_INVALID_ATTESTATION,  // 50% slash
        VAL_COLLUSION,            // 100% slash
        VAL_LIVENESS,             // 5% slash (automatic)
        AGENT_MALICIOUS_DEPLOY,   // 50% slash
        AGENT_POLICY_VIOLATION,   // 25% slash
        AGENT_FALSE_ATTESTATION   // 50% slash
    }

    enum EvidenceStatus {
        PENDING,
        UNDER_REVIEW,
        VOTE_OPEN,
        EXECUTED,
        DISMISSED,
        APPEALED
    }

    struct Evidence {
        uint256        id;
        address        whistleblower;
        address        accused;
        SlashCondition condition;
        bytes          proof;
        EvidenceStatus status;
        uint256        submittedAt;
        uint256        reviewDeadline;  // submittedAt + 48 hours
        uint256        voteDeadline;    // reviewDeadline + 72 hours
        bool           appealed;
        bytes          counterEvidence;
        // Vote tallies
        uint256        yesWeight;
        uint256        noWeight;
        mapping(address => bool) hasVoted;
    }

    // ─── Constants ───────────────────────────────────────────────────────────

    uint256 public constant REVIEW_PERIOD  = 48 hours;
    uint256 public constant VOTE_PERIOD    = 72 hours;
    uint256 public constant VOTE_QUORUM    = 10;  // % of circulating supply
    uint256 public constant VOTE_THRESHOLD = 60;  // % supermajority

    uint256 public constant BURN_SHARE        = 50;
    uint256 public constant WHISTLEBLOWER_SHARE = 25;
    uint256 public constant DAO_SHARE          = 25;

    uint256 public constant FILING_DEPOSIT = 100e18; // 100 $MAAT anti-spam deposit

    // ─── State ───────────────────────────────────────────────────────────────

    MaatToken public maatToken;
    address   public daoTreasury;
    address   public owner;

    uint256 public evidenceCount;
    mapping(uint256 => Evidence) public evidences;

    // ─── Events ──────────────────────────────────────────────────────────────

    event EvidenceSubmitted(
        uint256 indexed evidenceId,
        address indexed whistleblower,
        address indexed accused,
        SlashCondition  condition,
        uint256         timestamp
    );
    event AppealSubmitted(
        uint256 indexed evidenceId,
        address         accused,
        uint256         timestamp
    );
    event SlashVoted(
        uint256 indexed evidenceId,
        address indexed voter,
        bool            executeSlash,
        uint256         weight
    );
    event SlashExecuted(
        uint256 indexed evidenceId,
        address indexed accused,
        uint256         slashedAmount,
        uint256         burnedAmount,
        uint256         whistleblowerAmount,
        uint256         daoAmount
    );
    event SlashDismissed(
        uint256 indexed evidenceId,
        uint256         depositForfeited
    );
    event AutomaticSlashExecuted(
        address indexed accused,
        SlashCondition  condition,
        uint256         amount
    );

    // ─── Constructor ─────────────────────────────────────────────────────────

    constructor(address _maatToken, address _daoTreasury) {
        maatToken    = MaatToken(_maatToken);
        daoTreasury  = _daoTreasury;
        owner        = msg.sender;
    }

    // ─── Evidence Submission ─────────────────────────────────────────────────

    /// @notice Submit evidence of a slash condition.
    /// @dev Requires FILING_DEPOSIT to prevent spam.
    function submitEvidence(
        address        accused,
        SlashCondition condition,
        bytes calldata proof
    ) external returns (uint256 evidenceId) {
        require(accused != address(0), "Invalid accused");

        // Collect anti-spam deposit
        maatToken.transferFrom(msg.sender, address(this), FILING_DEPOSIT);

        evidenceId = ++evidenceCount;
        Evidence storage ev = evidences[evidenceId];
        ev.id             = evidenceId;
        ev.whistleblower  = msg.sender;
        ev.accused        = accused;
        ev.condition      = condition;
        ev.proof          = proof;
        ev.status         = EvidenceStatus.UNDER_REVIEW;
        ev.submittedAt    = block.timestamp;
        ev.reviewDeadline = block.timestamp + REVIEW_PERIOD;
        ev.voteDeadline   = ev.reviewDeadline + VOTE_PERIOD;

        // Double-vote: validate proof on-chain and slash immediately
        if (condition == SlashCondition.VAL_DOUBLE_VOTE) {
            _automaticSlash(accused, condition, ev);
            ev.status = EvidenceStatus.EXECUTED;
            return evidenceId;
        }

        emit EvidenceSubmitted(evidenceId, msg.sender, accused, condition, block.timestamp);
    }

    // ─── Appeal ──────────────────────────────────────────────────────────────

    /// @notice Accused may submit counter-evidence during the review period.
    function submitAppeal(
        uint256        evidenceId,
        bytes calldata counterEvidence_
    ) external {
        Evidence storage ev = evidences[evidenceId];
        require(ev.accused == msg.sender,                  "Not the accused");
        require(block.timestamp < ev.reviewDeadline,       "Review period expired");
        require(!ev.appealed,                              "Already appealed");
        require(ev.status == EvidenceStatus.UNDER_REVIEW,  "Invalid status");

        ev.counterEvidence = counterEvidence_;
        ev.appealed        = true;
        ev.status          = EvidenceStatus.APPEALED;

        emit AppealSubmitted(evidenceId, msg.sender, block.timestamp);
    }

    // ─── Governance Vote ─────────────────────────────────────────────────────

    /// @notice $MAAT stakers vote to execute or dismiss a slash.
    /// @param executeSlash true = execute slash; false = dismiss
    function vote(uint256 evidenceId, bool executeSlash) external {
        Evidence storage ev = evidences[evidenceId];
        require(
            ev.status == EvidenceStatus.UNDER_REVIEW ||
            ev.status == EvidenceStatus.APPEALED,
            "Not in votable state"
        );
        require(block.timestamp >= ev.reviewDeadline, "Review period not ended");
        require(block.timestamp <  ev.voteDeadline,   "Vote period expired");
        require(!ev.hasVoted[msg.sender],              "Already voted");

        uint256 weight = maatToken.votingWeight(msg.sender);
        require(weight > 0, "No voting weight");

        ev.hasVoted[msg.sender] = true;
        ev.status = EvidenceStatus.VOTE_OPEN;

        if (executeSlash) {
            ev.yesWeight += weight;
        } else {
            ev.noWeight  += weight;
        }

        emit SlashVoted(evidenceId, msg.sender, executeSlash, weight);
    }

    // ─── Slash Execution ─────────────────────────────────────────────────────

    /// @notice Finalize vote after vote period ends. Anyone may call.
    function finalizeVote(uint256 evidenceId) external {
        Evidence storage ev = evidences[evidenceId];
        require(ev.status == EvidenceStatus.VOTE_OPEN, "Not in vote-open state");
        require(block.timestamp >= ev.voteDeadline,    "Vote period not ended");

        uint256 totalVotes  = ev.yesWeight + ev.noWeight;
        uint256 totalSupply = maatToken.totalSupply();
        bool    quorumMet   = totalVotes * 100 >= totalSupply * VOTE_QUORUM;
        bool    majority    = totalVotes > 0 &&
                              ev.yesWeight * 100 >= totalVotes * VOTE_THRESHOLD;

        if (quorumMet && majority) {
            _executeSlash(ev);
        } else {
            _dismissSlash(ev);
        }
    }

    // ─── Automatic Slash (liveness, double-vote) ─────────────────────────────

    /// @notice Execute a liveness slash automatically (called at epoch end by protocol).
    function automaticLivenessSlash(address validator) external onlyOwner {
        uint256 stake  = maatToken.stakedBalanceOf(validator);
        uint256 amount = stake / 20; // 5%
        _distributeSlash(validator, amount, address(0));
        emit AutomaticSlashExecuted(validator, SlashCondition.VAL_LIVENESS, amount);
    }

    // ─── Internal ────────────────────────────────────────────────────────────

    function _automaticSlash(
        address        accused,
        SlashCondition condition,
        Evidence storage ev
    ) internal {
        uint256 stake  = maatToken.stakedBalanceOf(accused);
        uint256 amount = _computeSlashAmount(stake, condition);
        _distributeSlash(accused, amount, ev.whistleblower);
        emit AutomaticSlashExecuted(accused, condition, amount);
    }

    function _executeSlash(Evidence storage ev) internal {
        uint256 stake  = maatToken.stakedBalanceOf(ev.accused);
        uint256 amount = _computeSlashAmount(stake, ev.condition);
        ev.status = EvidenceStatus.EXECUTED;

        uint256 burnAmt  = amount * BURN_SHARE         / 100;
        uint256 wbAmt    = amount * WHISTLEBLOWER_SHARE / 100;
        uint256 daoAmt   = amount - burnAmt - wbAmt;

        // Execute the slash (burns the stake in MaatToken)
        maatToken.slash(ev.accused, amount);

        // Distribute whistleblower portion (mint equivalent from DAO reserve)
        // In production, the DAO treasury pre-funds the whistleblower reward.
        maatToken.transfer(ev.whistleblower, wbAmt);
        maatToken.transfer(daoTreasury,      daoAmt);
        // burnAmt is already burned by maatToken.slash()

        // Return filing deposit to whistleblower (they were right)
        maatToken.transfer(ev.whistleblower, FILING_DEPOSIT);

        emit SlashExecuted(ev.id, ev.accused, amount, burnAmt, wbAmt, daoAmt);
    }

    function _dismissSlash(Evidence storage ev) internal {
        ev.status = EvidenceStatus.DISMISSED;
        // Whistleblower loses filing deposit (goes to DAO treasury)
        maatToken.transfer(daoTreasury, FILING_DEPOSIT);
        emit SlashDismissed(ev.id, FILING_DEPOSIT);
    }

    function _distributeSlash(
        address accused,
        uint256 amount,
        address whistleblower
    ) internal {
        maatToken.slash(accused, amount);
        if (whistleblower != address(0)) {
            uint256 wbAmt  = amount * WHISTLEBLOWER_SHARE / 100;
            uint256 daoAmt = amount * DAO_SHARE            / 100;
            maatToken.transfer(whistleblower, wbAmt);
            maatToken.transfer(daoTreasury,   daoAmt);
        } else {
            uint256 daoAmt = amount * (WHISTLEBLOWER_SHARE + DAO_SHARE) / 100;
            maatToken.transfer(daoTreasury, daoAmt);
        }
    }

    function _computeSlashAmount(
        uint256        stake,
        SlashCondition condition
    ) internal pure returns (uint256) {
        if (condition == SlashCondition.VAL_DOUBLE_VOTE)         return stake;
        if (condition == SlashCondition.VAL_COLLUSION)           return stake;
        if (condition == SlashCondition.VAL_INVALID_ATTESTATION) return stake / 2;
        if (condition == SlashCondition.AGENT_MALICIOUS_DEPLOY)  return stake / 2;
        if (condition == SlashCondition.AGENT_FALSE_ATTESTATION) return stake / 2;
        if (condition == SlashCondition.AGENT_POLICY_VIOLATION)  return stake / 4;
        if (condition == SlashCondition.VAL_LIVENESS)            return stake / 20;
        return 0;
    }

    // ─── Modifiers ───────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
}
