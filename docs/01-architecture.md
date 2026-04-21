# Architecture

## High-Level Flow

1. Agent proposes deployment
2. AVM executes reasoning trace
3. Validators replay and verify
4. Consensus finalizes deployment
5. Production system unlocks deploy

## Components

### 1. Agent
- Generates reasoning trace
- Signs identity
- Stakes $MAAT

### 2. AVM
- Executes trace
- Produces deterministic output
- Validates against policy

### 3. Validators
- Re-run trace
- Vote on validity

### 4. Chain
- Stores:
  - Trace hash
  - Artifact hash
  - Policy reference
  - Signatures

### 5. Production Gate
- Only deploys if chain approves
