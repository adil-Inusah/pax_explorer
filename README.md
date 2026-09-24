# Pax Explorer

Pax Explorer is a governed metadata discovery, lineage analysis, and semantic catalog framework for IBM Planning Analytics / TM1. It inventories TM1 objects and structural definitions, parses TurboIntegrator processes and cube rules, validates discovered references, catalogs operational dependencies, and prepares graph-ready relationships for documentation and impact analysis.

The project separates source collection from semantic resolution. Collectors preserve evidence and publish stable current outputs only after complete runs. Profilers and future resolvers build derived meaning without overwriting the original parser artifacts.

## Current Project Status

The collection and structural-catalog foundation is complete.

| Domain | Verified inventory |
|---|---:|
| Core objects | 1,685 |
| Cubes | 379 |
| Dimensions | 902 |
| Processes | 386 |
| Chores | 18 |
| Attributes | 2,719 |
| Hierarchies | 927 |
| Public subsets | 10,201 |
| Public views | 2,059 |
| Chore tasks | 67 |
| Chore parameter bindings | 148 |
| Cube-dimension relationships | 1,154 |
| TI relationships | 4,019 |
| Rule relationships | 267 |

The corrected process data-source inventory covers all 386 processes, including 280 processes with configured sources and 107 unique source definitions.

The catalog-matching profiler currently evaluates 2,933 deferred references with full reconciliation and zero profiler errors. The latest verified classification distribution is:

```text
AMBIGUOUS_MATCH                 1
DEFAULT_HIERARCHY_MATCH     1,625
DYNAMIC_REFERENCE            557
EXACT_MATCH                   50
EXISTING_CREATE_TARGET       599
INSUFFICIENT_CONTEXT           1
MISSING_DELETE_TARGET         33
TARGET_NOT_IN_CATALOG         22
VALID_CREATE_TARGET           45
```

The profiler identifies 1,720 initial automatic-resolution candidates and 1,213 review or unresolved records.

## Capabilities

Pax Explorer currently supports:

- Core TM1 object inventory for cubes, dimensions, processes, and chores
- Regular and control object classification
- TurboIntegrator relationship parsing across Prolog, Metadata, Data, and Epilog
- Cube rule and feeder relationship parsing
- TI process parameter inventory and parameter-resolution provenance
- Attribute inventory across regular, control, default, and alternate hierarchies
- First-class hierarchy inventory
- Public static and MDX subset definition inventory using selective REST retrieval
- Public view identity inventory using selective REST retrieval
- Ordered cube-to-dimension structural relationships
- Ordered chore-task lineage and process parameter bindings
- Process data-source classification and credential-safe publication
- Operational file, command, executable, and script dependencies
- Quality exception governance
- Catalog match profiling for attributes, hierarchies, subsets, and views
- Complete-run publication protection and timestamped diagnostic snapshots
- Holistic smoke testing and automated regression testing

## Architecture

```text
TM1 Server
|
+-- Core Metadata
|   +-- Cubes
|   +-- Dimensions
|   +-- Processes
|   +-- Chores
|
+-- Structural Metadata
|   +-- Cube -> Dimension
|   +-- Dimension -> Hierarchy
|   +-- Hierarchy -> Attribute
|   +-- Hierarchy -> Public Subset
|   +-- Cube -> Public View
|
+-- TI Lineage
|   +-- Process procedures
|   +-- Direct relationships
|   +-- Parameter-derived relationships
|   +-- Relationship evidence
|
+-- Rule Lineage
|   +-- Rules
|   +-- Cross-cube references
|   +-- Hierarchy and attribute references
|   +-- Feeders
|
+-- Operational Lineage
|   +-- Chore tasks
|   +-- Process data sources
|   +-- Files
|   +-- Commands
|   +-- Executables and scripts
|
+-- Semantic Analysis
    +-- Catalog match profiles
    +-- Review queue
    +-- Future semantic validation plan
    +-- Future unified graph projection
```

The inventory layers answer **what exists**. Structural relationships answer **how TM1 objects are assembled**. TI, rule, chore, source, and operational relationships answer **how objects are used**. The semantic layer determines which references resolve safely to canonical catalog nodes.

## Repository Structure

```text
Pax_explorer/
├── config/
│   └── catalog_quality_exceptions.json
├── credentials/                         # Local only, never commit
├── data/
│   ├── current/                         # Latest complete governed outputs
│   ├── snapshots/                       # Timestamped run artifacts
│   └── review/                          # Optional local review exports
├── models/
│   ├── catalog.py
│   └── catalog_adapter.py
├── parsers/
│   ├── rule_parser.py
│   ├── ti_parameter_resolver.py
│   └── ti_parser.py
├── scripts/
│   ├── adapt_existing_catalog.py
│   ├── build_operational_dependencies.py
│   ├── check_tm1_connection.py
│   ├── collect_tm1_attributes.py
│   ├── collect_tm1_chore_tasks.py
│   ├── collect_tm1_cube_dimensions.py
│   ├── collect_tm1_hierarchies.py
│   ├── collect_tm1_metadata.py
│   ├── collect_tm1_process_data_sources.py
│   ├── collect_tm1_rule_lineage.py
│   ├── collect_tm1_subsets.py
│   ├── collect_tm1_ti_lineage.py
│   ├── collect_tm1_views.py
│   └── profile_tm1_catalog_matches.py
├── tests/
├── utilities/
│   └── tm1_connection.py
├── holistic_smoke_test.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Prerequisites

- Windows, Linux, or macOS with access to the TM1 REST API
- Python 3.12 recommended
- A TM1 account allowed to read the required cubes, dimensions, hierarchies, attributes, processes, procedures, rules, chores, views, and subsets
- A configured TM1 REST connection

The verified development environment uses Python 3.12.

## Installation

### 1. Clone and enter the repository

```powershell
git clone <repository-url>
cd Pax_explorer
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
```

### 3. Activate it

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Command Prompt:

```bat
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

## Configuration and Security

Copy the environment template:

```powershell
Copy-Item .env.example .env
```

Populate the variables defined in `.env.example`. The connection implementation is in:

```text
utilities/tm1_connection.py
```

Never commit:

```text
.env
credentials/
.venv/
__pycache__/
.pytest_cache/
data/current/
data/snapshots/
```

Generated metadata can contain object names, rule text, process code, file paths, SQL information, operational commands, and environment-specific details. Treat published catalog data as sensitive unless explicitly approved for version control.

The data-source and operational collectors redact likely credential values. Do not weaken these controls, and review generated outputs before external distribution.

## Verify the TM1 Connection

Run the connection test before collection:

```powershell
python -m scripts.check_tm1_connection
```

Resolve authentication, SSL, host, namespace, permission, and connectivity errors before running the governed pipeline.

## Governed Collection Workflow

Run collectors in dependency order.

### Phase A: Core catalogs

```powershell
python -m scripts.collect_tm1_metadata
python -m scripts.collect_tm1_ti_lineage
python -m scripts.collect_tm1_rule_lineage
```

### Phase B: First-class structure

```powershell
python -m scripts.collect_tm1_attributes
python -m scripts.collect_tm1_hierarchies --scope all
python -m scripts.collect_tm1_subsets
python -m scripts.collect_tm1_views
python -m scripts.collect_tm1_cube_dimensions --scope all
```

### Phase C: Operational lineage

```powershell
python -m scripts.collect_tm1_chore_tasks
python -m scripts.collect_tm1_process_data_sources
python -m scripts.build_operational_dependencies
```

### Phase D: Semantic profiling

```powershell
python -m scripts.profile_tm1_catalog_matches
```

For a diagnostic run that must not update `data/current`, add:

```powershell
--no-publish
```

Example:

```powershell
python -m scripts.profile_tm1_catalog_matches --no-publish
```

## Collector Details

### Core Metadata

```powershell
python -m scripts.collect_tm1_metadata
```

Publishes the canonical object inventory, including regular and control object splits.

Primary files include:

```text
objects.json
regular_objects.json
control_objects.json
manifest.json
metadata_errors.json
```

### TI Lineage

```powershell
python -m scripts.collect_tm1_ti_lineage
```

The TI collector:

- Retrieves every process definition
- Reads Prolog, Metadata, Data, and Epilog
- Parses relationship evidence
- Summarizes repeated references
- Resolves parameter-derived relationships
- Validates references against the object catalog
- Publishes parameter definitions, bindings, aliases, rejection inventories, and metrics

Typical relationships include:

```text
Process -> Process
Process -> Cube
Process -> Dimension
Process -> Hierarchy
Process -> Attribute
Process -> Subset
Process -> View
Process -> File
Process -> Command
```

### Rule Lineage

```powershell
python -m scripts.collect_tm1_rule_lineage
```

The rule collector parses rules and feeders, preserves evidence, summarizes graph-ready relationships, and validates references against the current catalog.

### Attributes

```powershell
python -m scripts.collect_tm1_attributes
```

The verified inventory contains 2,719 attributes across 902 dimensions and 927 hierarchies.

### Hierarchies

```powershell
python -m scripts.collect_tm1_hierarchies --scope all
```

Publishes one first-class hierarchy node and one `BELONGS_TO_DIMENSION` relationship per hierarchy.

### Public Subsets

```powershell
python -m scripts.collect_tm1_subsets
```

The collector uses selective REST retrieval and does not expand static subset members during routine catalog collection. It classifies static and MDX subsets and publishes hierarchy and dimension ownership relationships.

Verified inventory:

```text
Public subsets: 10,201
Relationships:  20,402
Validations:    20,402
```

### Public Views

```powershell
python -m scripts.collect_tm1_views
```

The current view collector performs a selective public identity inventory. Subtype-specific native and MDX definition enrichment is intentionally deferred.

Verified inventory:

```text
Public views:  2,059
Relationships: 2,059
Validations:   2,059
```

### Cube Dimensions

```powershell
python -m scripts.collect_tm1_cube_dimensions --scope all
```

Publishes ordered structural edges:

```text
Cube -> USES_DIMENSION -> Dimension
```

Each edge retains `dimension_position`.

Verified inventory:

```text
Cubes:         379
Relationships: 1,154
Validations:   1,154
```

### Chore Tasks

```powershell
python -m scripts.collect_tm1_chore_tasks
```

Publishes:

```text
Chore -> HAS_TASK -> ChoreTask
ChoreTask -> CALLS_PROCESS -> Process
ChoreTask -> PASSES_PARAMETER -> ProcessParameter
```

Verified inventory:

```text
Chores:         18
Tasks:          67
Bindings:      148
Relationships: 282
Validations:   282
```

### Process Data Sources

```powershell
python -m scripts.collect_tm1_process_data_sources
```

The collector reads flattened TM1py `datasource_*` fields and classifies sources such as ASCII/file, ODBC, cube view, dimension subset, JSON, none, or unknown.

Verified inventory:

```text
Processes:          386
Configured:         280
Unique data sources:107
Relationships:      375
Validations:        375
```

Plaintext passwords are never published. Usernames are represented by presence flags and hashes where applicable.

### Operational Dependencies

```powershell
python -m scripts.build_operational_dependencies
```

Normalizes files, scripts, commands, and executables without executing commands or accessing external paths.

Relationship examples:

```text
Process -> WRITES_FILE -> File
Process -> EXECUTES_COMMAND -> Command
Command -> INVOKES_EXECUTABLE -> Executable
Command -> REFERENCES_SCRIPT -> Script
```

### Catalog Match Profiler

```powershell
python -m scripts.profile_tm1_catalog_matches
```

The profiler is analytical and non-destructive. It does not overwrite TI or rule validations. It evaluates deferred attribute, hierarchy, subset, and view references against the governed catalogs.

Supported join methods:

```text
RELATIONSHIP_ID
SEMANTIC_KEY
SEMANTIC_GROUP_ORDINAL
```

Supported classifications:

```text
EXACT_MATCH
DEFAULT_HIERARCHY_MATCH
UNIQUE_HIERARCHY_MATCH
UNIQUE_GLOBAL_MATCH
AMBIGUOUS_MATCH
DYNAMIC_REFERENCE
TARGET_NOT_IN_CATALOG
VALID_CREATE_TARGET
EXISTING_CREATE_TARGET
MISSING_DELETE_TARGET
INSUFFICIENT_CONTEXT
```

The profiler understands compact parser targets such as:

```text
Employee.Caption
}Processes.Source File
Fiscal Period.sSrcView
Balance Sheet.sTgtView
```

## Publication Contract

Collectors and builders use these states:

- `COMPLETE`: all requested records were processed successfully
- `PARTIAL`: diagnostic snapshot written, current known-good output preserved
- `FAILED`: primary workflow failed
- `RUNNING`: collector started but has not completed, where supported

Only `COMPLETE` runs replace governed files under `data/current`.

A typical safe workflow is:

```powershell
python -m scripts.collect_tm1_subsets --no-publish
# Review snapshot outputs
python -m scripts.collect_tm1_subsets
```

## Generated Data

### Snapshot outputs

Every run writes timestamped diagnostics under:

```text
data/snapshots/<snapshot-id>/
```

TI and rule collectors retain source-specific subdirectories. Newer first-class collectors publish their domain artifacts at the snapshot root.

### Current outputs

A complete run publishes the latest governed artifacts under:

```text
data/current/
```

Major current artifact groups include:

```text
Core metadata
TI and rule lineage
Attributes
Hierarchies
Public subsets
Public views
Cube dimensions
Chore tasks
Process data sources
Operational dependencies
Catalog match profiles
```

Source-specific prefixes prevent collectors from overwriting one another.

## Evidence, Relationships, and Validations

Pax Explorer preserves three levels of meaning:

### Evidence

Evidence records retain discovery context such as source procedure, line, expression, TM1 function, feeder state, and confidence.

### Summarized relationships

Summary records collapse repeated evidence into graph-ready relationships while retaining counts and provenance.

### Validations

Validation records preserve whether the reference is valid, deferred, dynamic, missing, ambiguous, external, or accepted through a governed exception.

Every published relationship domain must reconcile:

```text
relationship count = validation count
```

## Quality Exceptions

Governed exceptions are stored in:

```text
config/catalog_quality_exceptions.json
```

Exceptions must be explicit and traceable. They must not silently hide unregistered missing targets.

## Testing

Run the complete suite:

```powershell
python -m pytest -q
```

The latest verified baseline before this README update was 173 passing tests, subject to the exact checked-out test set.

Run profiler tests:

```powershell
python -m pytest \
    .\tests\test_profile_tm1_catalog_matches.py \
    .\tests\test_profile_tm1_catalog_context.py \
    -q
```

Run verbose tests:

```powershell
python -m pytest -v
```

Run coverage when `pytest-cov` is installed:

```powershell
python -m pytest \
    --cov=models \
    --cov=parsers \
    --cov=scripts \
    --cov=utilities \
    --cov-report=term-missing
```

## Holistic Smoke Test

Run:

```powershell
python .\holistic_smoke_test.py \
    .\data\current \
    --exceptions .\config\catalog_quality_exceptions.json
```

The smoke test validates parseability, catalog counts, relationship-validation reconciliation, exception governance, and selected structural invariants. Extend the smoke test whenever a new governed domain is added.

## Syntax Validation

Compile changed modules before collection:

```powershell
python -m py_compile \
    .\scripts\profile_tm1_catalog_matches.py \
    .\tests\test_profile_tm1_catalog_matches.py \
    .\tests\test_profile_tm1_catalog_context.py
```

No output indicates successful compilation.

## Troubleshooting

### A run is `PARTIAL`

Inspect the timestamped snapshot. Partial runs intentionally do not replace `data/current`.

### The process data-source inventory reports zero configured sources

Confirm that the collector reads flattened TM1py process properties such as:

```text
datasource_type
datasource_query
datasource_view
datasource_subset
datasource_data_source_name_for_server
```

### A selective REST request returns HTTP 400

Use only server-supported structural fields in `$select`. For public views, the current identity collector requests `Name` only because `@odata.type` is an annotation and is not accepted in the `$select` list by the verified environment.

### Profiler returns join errors

Inspect `catalog_match_errors.json`. The profiler supports explicit IDs, unique semantic keys, and duplicate semantic groups. Group-size mismatches remain errors by design.

### Import errors

Run commands from the repository root with the project virtual environment active.

## Current Limitations

- Dynamic TI references remain unresolved unless existing parameter provenance can determine a safe value.
- Public view collection currently inventories identities only; native-axis and MDX definition enrichment remain future work.
- Private subsets and views are intentionally excluded from the governed public catalogs.
- Element-level hierarchy membership is not cataloged as first-class graph structure.
- External files, commands, and ODBC sources are identified but not physically cross-checked.
- Existing create targets and missing delete targets require procedure-aware review.
- The semantic resolution plan and unified graph projection are not yet published.
- Rule and TI parser coverage can continue to expand as additional TM1 functions are identified.

## Next Development Phase

The next phase is semantic resolution and unified graph construction.

Recommended order:

1. Publish the completed catalog match profile
2. Generate a non-destructive semantic validation resolution plan
3. Apply only approved automatic resolutions to derived semantic validation files
4. Preserve original parser validations unchanged
5. Build unified graph nodes
6. Build unified graph relationships and validations
7. Add graph integrity and secret-scanning controls
8. Add direct and transitive impact-analysis queries
9. Add source-snapshot comparison and change detection
10. Add searchable documentation and visualization

Planned semantic resolution outputs:

```text
catalog_validation_resolution_plan.json
catalog_validation_resolution_summary.json
catalog_validation_resolution_review.csv
catalog_validation_resolution_manifest.json
catalog_validation_resolution_errors.json
```

Initial automatic mappings:

```text
EXACT_MATCH
    -> VALID

DEFAULT_HIERARCHY_MATCH
    -> VALID_DEFAULT_HIERARCHY_MATCH

VALID_CREATE_TARGET
    -> VALID_CREATE_TARGET
```

Review-required classifications include dynamic references, existing create targets, missing delete targets, catalog misses, ambiguous references, and insufficient context.

## Definition of Lineage Completeness

Pax Explorer is lineage-complete when the governed graph can trace discoverable relationships across:

```text
Cubes
Dimensions
Hierarchies
Attributes
Processes
Rules
Feeders
Chores
Chore tasks
Views
Subsets
Data sources
Files
Commands
Executables
Scripts
```

The target graph must support:

- Object documentation
- Upstream and downstream dependency analysis
- Change impact assessment
- Operational flow analysis
- Catalog search and navigation
- Traceable validation and review decisions
- Snapshot comparison
- Simplified architecture diagrams

## Git Workflow

Review changes:

```powershell
git status --short
git diff --stat
git diff
```

Stage intended files:

```powershell
git add -A
git status
git diff --cached --stat
```

Do not stage generated current or snapshot outputs unless repository policy explicitly requires versioning them.

## Contributing

Before submitting a change:

1. Activate the virtual environment
2. Install the current requirements
3. Compile changed Python files
4. Run focused tests
5. Run the complete suite
6. Run the holistic smoke test
7. Inspect generated JSON and review queues
8. Confirm relationship-validation reconciliation
9. Confirm no secrets appear in generated artifacts
10. Update this README when commands, files, outputs, or contracts change
