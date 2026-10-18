---
doc_type: maintenance-contract
---
# Shared framework rules

## Inputs

- Working: named rule change and affected Role/Company scope.
- Reference: the exact section of the packaged [baseline.md](../../src/nplet/resources/organization/baseline.md) or the contributor [baseline.md](baseline.md); selected owning unit in [Teams](../../src/nplet/resources/organization/team-map.md#teams) or [Company-level staff placement](../../src/nplet/resources/organization/team-map.md#placement-and-authority); selected numbered Role entry and, if relevant, Profile row in [role-coverage.md](role-coverage.md#canonical-role-inventory).
- For changed definition semantics: [field guidance](../../src/nplet/resources/templates/definition-format.md#role-field-guidance) and [review criteria](../authoring/definition-format.md#review-and-maintenance).

### Rule topics

- [Planning and assignment](../../src/nplet/resources/organization/baseline.md#planning-and-wave-boundaries) · [Work Item Plan](../../src/nplet/resources/organization/baseline.md#work-item-plan-contract) · [Model and capacity](../../src/nplet/resources/organization/baseline.md#model-and-capacity-boundaries)
- [Authority](../../src/nplet/resources/organization/baseline.md#authority-and-role-boundaries) · [Sources](../../src/nplet/resources/organization/baseline.md#source-authority-and-retrieval)
- [Context and independence](../../src/nplet/resources/organization/baseline.md#context-and-independent-assurance) · [Assurance ownership](../../src/nplet/resources/organization/baseline.md#assurance-ownership)
- [Governance and technical ownership](../../src/nplet/resources/organization/baseline.md#governance-and-technical-ownership) · [Handoffs](../../src/nplet/resources/organization/baseline.md#handoffs-and-outcomes)
- [Working locations](baseline.md#working-locations-and-ownership) · [Publication](baseline.md#authoring-and-publication-rules)
- [Paper scope](baseline.md#selected-paper-fixtures-and-exclusions) · [Setup](baseline.md#setup-boundary) · [Evidence and privacy](../../src/nplet/resources/organization/baseline.md#evidence-and-privacy)

## Process

1. Locate the owning public section and read that section, not the whole Role inventory.
2. Reconcile the scoped change with its authority, source and independence constraints; retain unresolved decisions.
3. Update affected links and explicit consumers. Follow only the selected example/fixture when that scope is affected.

## Outputs

Assigned canonical rule/placement/coverage edits and the exact diff. The baseline owns shared rules; team-map owns placement; role-coverage owns fixture participation.

## Human check

Use the existing PR check in [the maintenance contract](../CONTEXT.md#human-check) to confirm unchanged adjacent authority and complete affected references.
