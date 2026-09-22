# Pax Explorer

Pax Explorer is a metadata discovery and lineage analysis framework for IBM Planning Analytics / TM1. The project collects TM1 object inventory, parses TurboIntegrator process code and cube rules, validates discovered references, and converts the results into a unified catalog model for documentation, dependency analysis, and future visualization.

## Current Capabilities

Pax Explorer currently supports three collection layers:

1. **Metadata inventory**: discovers TM1 cubes, dimensions, processes, and chores.
2. **TurboIntegrator lineage**: analyzes TI procedure code to identify process-driven relationships.
3. **Cube rule lineage**: analyzes cube rules and feeders to identify calculation-driven relationships.

The collected data can be adapted into a unified catalog model through the existing catalog adapter.

## Architecture

```text
TM1 Server
    |
    +-- Metadata Collector
    |       +-- Cubes
    |       +-- Dimensions
    |       +-- Processes
    |       +-- Chores
    |
    +-- TI Lineage Collector
    |       +-- Prolog
    |       +-- Metadata
    |       +-- Data
    |       +-- Epilog
    |
    +-- Rule Lineage Collector
            +-- Cube rules
            +-- Cross-cube references
            +-- Dimension references
            +-- Hierarchy references
            +-- Attribute references
            +-- Feeders

Collected JSON
    |
    +-- Snapshot history
    +-- Current published results
    +-- Unified catalog adapter
```

The inventory layer answers **what objects exist**. The lineage layers answer **how those objects are related**.

## Repository Structure

```text
Pax_explorer/
├── config/                         # Application configuration
├── credentials/                    # Local credential resources, not committed
├── data/                           # Generated snapshots and current outputs
├── models/
│   ├── __init__.py
│   ├── catalog.py                  # Unified catalog domain model
│   └── catalog_adapter.py          # Existing JSON-to-catalog adapter
├── parsers/
│   ├── __init__.py
│   ├── rule_parser.py              # TM1 cube rule and feeder parser
│   └── ti_parser.py                # TurboIntegrator code parser
├── scripts/
│   ├── __init__.py
│   ├── adapt_existing_catalog.py   # Builds unified catalog output
│   ├── check_tm1_connection.py     # Tests the TM1 connection
│   ├── collect_tm1_metadata.py     # Collects TM1 object inventory
│   ├── collect_tm1_rule_lineage.py # Collects cube rule lineage
│   └── collect_tm1_ti_lineage.py   # Collects TI process lineage
├── tests/                          # Automated tests
├── utilities/                      # Shared TM1 connection utilities
├── .env                            # Local environment values, not committed
├── .env.example                    # Environment variable template
├── .gitignore
├── requirements.txt
└── README.md
```

## Prerequisites

- Windows, Linux, or macOS with network access to the TM1 REST API
- Python 3.12 recommended
- A TM1 account with sufficient permission to read:
  - Cubes and cube rules
  - Dimensions
  - Processes and TI procedure text
  - Chores
- A configured TM1 REST API connection

The current verified development environment uses Python 3.12.10.

## Installation

### 1. Clone the repository

```powershell
git clone <repository-url>
cd Pax_explorer
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
```

### 3. Activate the virtual environment

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Command Prompt:

```cmd
.venv\Scripts\activate.bat
```

Bash or Zsh:

```bash
source .venv/bin/activate
```

### 4. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configuration

Copy the environment template and populate the local values:

```powershell
Copy-Item .env.example .env
```

Use the variable names already defined in `.env.example`. Do not commit `.env` or credential files.

The connection implementation is located in:

```text
utilities/tm1_connection.py
```

Confirm that `.gitignore` protects at least:

```gitignore
.env
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
credentials/*
data/current/
data/snapshots/
```

Generated TM1 metadata can contain object names, process source code, cube rules, and environment-specific details. Treat generated lineage data as potentially sensitive.

## Verify the TM1 Connection

Run the connection check before starting collection:

```powershell
python .\scripts\check_tm1_connection.py
```

Resolve authentication, SSL, host, port, namespace, or permission errors before running the collectors.

## Collection Workflow

Run the metadata collector first. The TI and rule lineage collectors require the current object catalog for relationship validation.

```text
1. Connection check
2. Metadata collection
3. TI lineage collection
4. Rule lineage collection
5. Catalog adaptation
```

### Step 1: Collect TM1 Metadata

```powershell
python .\scripts\collect_tm1_metadata.py
```

The metadata collector retrieves object names for:

- Cubes
- Dimensions
- Processes
- Chores

The collector publishes a current object inventory only when the collection completes successfully.

Primary output:

```text
data/current/objects.json
```

### Step 2: Collect TI Process Lineage

```powershell
python .\scripts\collect_tm1_ti_lineage.py
```

The TI collector:

1. Retrieves all TM1 processes.
2. Reads the Prolog, Metadata, Data, and Epilog procedures.
3. Passes the procedure text to `parsers/ti_parser.py`.
4. Creates detailed evidence records.
5. Summarizes repeated relationships.
6. Validates referenced objects against `data/current/objects.json`.
7. Preserves errors by process.

Typical TI relationships include:

```text
Process -> Cube
Process -> Dimension
Process -> View
Process -> Subset
Process -> Process
```

### Step 3: Collect Cube Rule Lineage

```powershell
python .\scripts\collect_tm1_rule_lineage.py
```

The rule collector:

1. Retrieves all TM1 cubes.
2. Reads the available cube rule text.
3. Passes the rule text to `parsers/rule_parser.py`.
4. Separates detailed evidence from summarized relationships.
5. Identifies ordinary rule references and feeder relationships.
6. Validates target references against the current object catalog.
7. Preserves errors by cube.

Typical rule relationships include:

```text
Cube -> Cube
Cube -> Dimension
Cube -> Hierarchy
Cube -> Attribute
Cube -> Feeder target
```

### Step 4: Adapt Existing Results into the Unified Catalog

After current metadata and lineage files are available, run:

```powershell
python .\scripts\adapt_existing_catalog.py `
    --environment <environment-name> `
    --database <tm1-database-name>
```

Example:

```powershell
python .\scripts\adapt_existing_catalog.py `
    --environment STAGING `
    --database Finance
```

By default, the adapter reads from:

```text
data/current/
```

and writes unified catalog files under:

```text
data/catalog/
```

Use the optional arguments when different folders are required:

```powershell
python .\scripts\adapt_existing_catalog.py `
    --environment STAGING `
    --database Finance `
    --current-root .\data\current `
    --output-root .\data\catalog
```

## Generated Data

### Snapshot Data

Each collection run creates timestamped output under `data/snapshots`.

```text
data/snapshots/<snapshot-id>/
├── ti_lineage/
│   ├── process_definitions.json
│   ├── relationship_evidence.json
│   ├── relationships.json
│   ├── relationship_validations.json
│   ├── errors.json
│   └── manifest.json
└── rule_lineage/
    ├── cube_rule_definitions.json
    ├── relationship_evidence.json
    ├── relationships.json
    ├── relationship_validations.json
    ├── errors.json
    └── manifest.json
```

Metadata inventory files are stored at the applicable snapshot root created by `collect_tm1_metadata.py`.

### Current Published Data

A complete run publishes its latest usable output under `data/current`.

Expected structure:

```text
data/current/
├── objects.json
├── manifest.json
├── ti_process_definitions.json
├── ti_relationship_evidence.json
├── ti_relationships.json
├── ti_relationship_validations.json
├── ti_lineage_manifest.json
├── cube_rule_definitions.json
├── rule_relationship_evidence.json
├── rule_relationships.json
├── rule_relationship_validations.json
└── rule_lineage_manifest.json
```

The TI and rule prefixes prevent one lineage source from overwriting another in the shared current directory.

## Snapshot and Publication Behavior

Collector manifests use the following status values:

- `COMPLETE`: all requested objects were collected and processed successfully.
- `PARTIAL`: one or more objects failed, but diagnostic snapshot files were produced.
- `FAILED`: the collection could not complete its primary workflow.
- `RUNNING`: the metadata collector has started but has not finished.

Only complete lineage runs should replace files under `data/current`. Partial results remain in the timestamped snapshot folder for diagnosis.

This publication model protects the latest known-good result from incomplete collection runs.

## Rule Parser Coverage

The initial rule parser recognizes the following TM1 functions:

### Cross-cube data references

```text
DB
```

A normal `DB` reference produces a `READS_FROM` relationship. A `DB` call found in a feeder statement produces a `FEEDS` relationship.

### Attribute references

```text
ATTRS
ATTRN
```

These functions produce `USES_ATTRIBUTE` evidence and retain the referenced dimension and attribute when those arguments are string literals.

### Dimension references

```text
DIMIX
DIMNM
DIMSIZ
```

These functions produce `REFERENCES_DIMENSION` evidence.

### Element and hierarchy references

```text
ELPAR
ELCOMP
ELISANC
ELLEV
```

These functions currently produce `REFERENCES_HIERARCHY` evidence.

### Parser behavior

The parser currently supports:

- Case-insensitive TM1 function matching
- Multiline function calls
- Nested function calls
- Balanced-parenthesis extraction
- String-aware argument splitting
- `#`, `//`, and `/* ... */` comment masking
- Source line numbers
- Detailed source expressions
- Feeder detection
- Literal target extraction
- Dynamic target preservation
- Relationship summarization

Dynamic targets are preserved rather than discarded. For example:

```text
DB(IF(condition, 'Actual Cube', 'Plan Cube'), ...)
```

produces a relationship with the original target expression and a target name that remains unresolved until static-expression analysis is added.

## Relationship Evidence and Summaries

Pax Explorer intentionally stores information at two levels.

### Evidence records

Evidence records preserve where and how a relationship was discovered, including:

- Source object
- Target object or expression
- TM1 function
- Relationship type
- Source line number
- Original expression
- Feeder status
- Confidence level

### Summarized relationships

Summary records collapse repeated evidence into graph-ready relationships while retaining an evidence count and first observed line.

For example, 15 references from one cube to another can be represented as:

```text
15 evidence records
1 summarized relationship
```

This supports both detailed auditability and simplified dependency diagrams.

## Validation

Lineage relationships are evaluated against the current metadata inventory.

The rule parser currently distinguishes between:

- Literal targets ready for catalog lookup
- Dynamic targets requiring additional resolution

Initial rule validation statuses include:

```text
PENDING_CATALOG_LOOKUP
PENDING_DYNAMIC_RESOLUTION
```

Catalog-aware existence checks and normalized lookup behavior remain an active implementation area.

## Running Tests

Run the complete test suite from the repository root:

```powershell
python -m pytest
```

Run with verbose test names:

```powershell
python -m pytest -v
```

Run an individual parser test module:

```powershell
python -m pytest .\tests\test_rule_parser.py -v
python -m pytest .\tests\test_ti_parser.py -v
```

Run coverage if `pytest-cov` is installed:

```powershell
python -m pytest --cov=models --cov=parsers --cov=scripts --cov=utilities --cov-report=term-missing
```

Current verified baseline:

```text
56 tests passed
```

The existing suite covers the catalog, catalog adapter, TM1 connection checks, metadata collector, rule parser, and TI parser. Dedicated tests for the TI and rule lineage collectors should be added next.

## Syntax Validation

Compile the primary scripts and parsers without executing a TM1 collection:

```powershell
python -m py_compile .\scripts\collect_tm1_metadata.py
python -m py_compile .\scripts\collect_tm1_ti_lineage.py
python -m py_compile .\scripts\collect_tm1_rule_lineage.py
python -m py_compile .\parsers\ti_parser.py
python -m py_compile .\parsers\rule_parser.py
```

No console output indicates a successful compilation.

## Troubleshooting

### Python cannot open a collector file

Run collectors from the repository root and use the `scripts` directory:

```powershell
python .\scripts\collect_tm1_ti_lineage.py
python .\scripts\collect_tm1_rule_lineage.py
```

Do not use `collectors` unless the project structure is intentionally changed.

### Current object catalog was not found

Run the metadata collector first:

```powershell
python .\scripts\collect_tm1_metadata.py
```

Confirm that this file exists:

```text
data/current/objects.json
```

### A lineage run is marked PARTIAL

Inspect the run-specific error file:

```text
data/snapshots/<snapshot-id>/ti_lineage/errors.json
data/snapshots/<snapshot-id>/rule_lineage/errors.json
```

A partial run is retained for diagnosis but should not replace the latest complete output.

### Rule text is not detected

Review the TM1py cube object returned by the connected environment. The rule collector normalizes several likely properties, but TM1py version differences or mocked objects may expose rule text differently.

Add a new property mapping in `get_cube_rule_text()` only after confirming the actual cube object shape.

### Import errors

Run commands from the project root so the scripts can establish the correct root path and resolve imports from `models`, `parsers`, and `utilities`.

Also confirm that the virtual environment is active and dependencies are installed:

```powershell
python -m pip install -r requirements.txt
```

## Git Workflow

After renaming or adding collectors, review Git's interpretation of the changes:

```powershell
git status
git diff --stat
git diff
```

Stage all intended changes:

```powershell
git add -A
```

Review the staged result:

```powershell
git status
git diff --cached --stat
git diff --cached
```

Git may display a manually renamed file as a deletion and addition until staging and similarity detection are applied.

## Current Limitations

- Rule parser function coverage is not yet complete.
- Dynamic cube and dimension expressions are preserved but not fully resolved.
- Rule validation is not yet fully connected to a canonical catalog lookup contract.
- Chore execution lineage is not yet collected as a separate relationship source.
- View and subset definitions are not yet independently cataloged.
- Rule relationships currently focus on recognized function calls.
- Dedicated collector-level tests for TI and rule collection are still needed.
- Rule retrieval must be validated against the specific TM1py version used by the target environment.

## Planned Development

Recommended implementation order:

1. Validate rule collection against a live TM1 environment.
2. Add tests for `collect_tm1_ti_lineage.py`.
3. Add tests for `collect_tm1_rule_lineage.py`.
4. Complete catalog-aware rule relationship validation.
5. Review all TM1 rule functions that can expose dependencies.
6. Add hierarchy-specific function coverage.
7. Add static analysis for dynamic target expressions.
8. Add chore-to-process lineage.
9. Add view and subset lineage.
10. Merge source-specific relationships into a unified graph.
11. Generate cube, process, and dependency documentation.
12. Add dependency diagrams and impact-analysis queries.

The formal rule-function review should confirm every available path for detecting:

- Cube reads
- Cube feeders
- Dimension use
- Hierarchy use
- Element relationships
- Attribute reads
- Dynamic object references
- Conditional target references

## Definition of Lineage Completeness

Pax Explorer will be considered lineage-complete when the catalog can trace all relationships discoverable from available TM1 metadata across:

```text
Cubes
Dimensions
Hierarchies
Elements
Attributes
TI processes
Cube rules
Feeders
Chores
Views
Subsets
```

The target outcome is a graph that supports:

- Object documentation
- Upstream and downstream dependency analysis
- Change impact assessment
- Operational flow analysis
- Simplified architecture diagrams
- Catalog search and navigation

## Security Considerations

Do not commit:

- `.env`
- Passwords or API credentials
- Authentication tokens
- Private certificates or keys
- Generated rule source from controlled environments
- Generated process source from controlled environments
- Production metadata snapshots unless explicitly approved

Use `.env.example` to document required variable names without including secret values.

## Contributing

Before submitting a change:

1. Activate the virtual environment.
2. Install the current requirements.
3. Run syntax validation.
4. Run the complete test suite.
5. Inspect generated JSON when collector behavior changes.
6. Update parser coverage documentation when adding TM1 functions.
7. Update this README when commands, filenames, or output contracts change.

Suggested validation sequence:

```powershell
python -m py_compile .\parsers\rule_parser.py
python -m py_compile .\parsers\ti_parser.py
python -m py_compile .\scripts\collect_tm1_rule_lineage.py
python -m py_compile .\scripts\collect_tm1_ti_lineage.py
python -m pytest
```

## Project Status

Pax Explorer is under active development. The metadata inventory, catalog model, TI parser, rule parser, and source-specific lineage collection structure are established. The next milestone is validating the rule collector against live TM1 rules and completing the review of all TM1 functions that can reveal object relationships.
