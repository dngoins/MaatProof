// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title DeployPolicy — MaatProof Deployment Policy Contract
/// @notice On-chain deployment policy rules for ACI/ACD governance.
///         The AVM and PoD validators query and evaluate this contract
///         as part of every deployment verification.
contract DeployPolicy {

    // ─── Structs ────────────────────────────────────────────────────────────

    struct PolicyRules {
        bool    noFridayDeploys;
        bool    requireHumanApproval;
        uint8   minTestCoverage;       // 0–100 (percent)
        uint8   maxCriticalCves;
        uint256 minAgentStake;         // in $MAAT (wei)
        uint8   deployWindowStart;     // UTC hour (0–23)
        uint8   deployWindowEnd;       // UTC hour (0–23)
        bool    requireSbom;
        uint8   maxHighCves;
    }

    // ─── State ───────────────────────────────────────────────────────────────

    PolicyRules public rules;
    uint256     public policyVersion;
    address     public policyOwner;
    bool        public active;

    /// @notice History of all policy versions (version → block number)
    mapping(uint256 => uint256) public versionHistory;

    // ─── Events ──────────────────────────────────────────────────────────────

    /// @notice Emitted every time a policy update is applied.
    event PolicyUpdated(
        uint256 indexed version,
        address indexed updatedBy,
        uint256          timestamp
    );

    /// @notice Emitted on every policy evaluation by the AVM / validators.
    event PolicyEvaluated(
        address indexed agent,
        bytes32 indexed traceHash,
        bool            passed,
        string          failReason,
        uint256         policyVersion
    );

    /// @notice Emitted when ownership is transferred.
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    /// @notice Emitted when the policy is deactivated.
    event PolicyDeactivated(address indexed deactivatedBy, uint256 timestamp);

    // ─── Constructor ─────────────────────────────────────────────────────────

    constructor(PolicyRules memory _rules) {
        rules         = _rules;
        policyVersion = 1;
        policyOwner   = msg.sender;
        active        = true;
        versionHistory[1] = block.number;
    }

    // ─── Core Evaluation ─────────────────────────────────────────────────────

    /// @notice Evaluate whether a deployment satisfies this policy.
    /// @dev Called by AVM and validators during PoD consensus.
    ///      Emits PolicyEvaluated on every call (on-chain audit record).
    /// @return passed     true if all rules pass
    /// @return failReason empty string if passed; failure code otherwise
    function evaluate(
        address agent,
        bytes32 traceHash,
        uint8   testCoverage,
        uint8   criticalCves,
        uint8   highCves,
        uint256 agentStake,
        bool    humanApprovalPresent,
        bool    sbomPresent,
        uint8   deployHourUtc,
        uint8   deployDayOfWeek        // 0 = Sunday … 5 = Friday … 6 = Saturday
    )
        external
        policyActive
        returns (bool passed, string memory failReason)
    {
        if (rules.noFridayDeploys && deployDayOfWeek == 5) {
            return _reject(agent, traceHash, "NO_FRIDAY_DEPLOYS");
        }
        if (rules.requireHumanApproval && !humanApprovalPresent) {
            return _reject(agent, traceHash, "HUMAN_APPROVAL_REQUIRED");
        }
        if (testCoverage < rules.minTestCoverage) {
            return _reject(agent, traceHash, "INSUFFICIENT_TEST_COVERAGE");
        }
        if (criticalCves > rules.maxCriticalCves) {
            return _reject(agent, traceHash, "CRITICAL_CVE_FOUND");
        }
        if (highCves > rules.maxHighCves) {
            return _reject(agent, traceHash, "HIGH_CVE_THRESHOLD_EXCEEDED");
        }
        if (agentStake < rules.minAgentStake) {
            return _reject(agent, traceHash, "INSUFFICIENT_AGENT_STAKE");
        }
        if (deployHourUtc < rules.deployWindowStart || deployHourUtc >= rules.deployWindowEnd) {
            return _reject(agent, traceHash, "OUTSIDE_DEPLOY_WINDOW");
        }
        if (rules.requireSbom && !sbomPresent) {
            return _reject(agent, traceHash, "SBOM_REQUIRED");
        }

        emit PolicyEvaluated(agent, traceHash, true, "", policyVersion);
        return (true, "");
    }

    // ─── Policy Management ───────────────────────────────────────────────────

    /// @notice Update policy rules. Increments policyVersion.
    /// @dev Agents must re-query and use the new version after this call.
    function updatePolicy(PolicyRules memory _rules) external onlyOwner {
        rules = _rules;
        policyVersion++;
        versionHistory[policyVersion] = block.number;
        emit PolicyUpdated(policyVersion, msg.sender, block.timestamp);
    }

    /// @notice Deactivate this policy (all evaluations will revert).
    function deactivate() external onlyOwner {
        active = false;
        emit PolicyDeactivated(msg.sender, block.timestamp);
    }

    /// @notice Transfer policy ownership (e.g., to a DAO multisig).
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(policyOwner, newOwner);
        policyOwner = newOwner;
    }

    // ─── View Helpers ────────────────────────────────────────────────────────

    /// @notice Returns the block number at which a specific version was set.
    function versionBlock(uint256 version) external view returns (uint256) {
        return versionHistory[version];
    }

    // ─── Internal Helpers ────────────────────────────────────────────────────

    function _reject(
        address agent,
        bytes32 traceHash,
        string memory reason
    ) internal returns (bool, string memory) {
        emit PolicyEvaluated(agent, traceHash, false, reason, policyVersion);
        return (false, reason);
    }

    // ─── Modifiers ───────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == policyOwner, "Not policy owner");
        _;
    }

    modifier policyActive() {
        require(active, "Policy is deactivated");
        _;
    }
}
