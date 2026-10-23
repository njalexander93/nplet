---
doc_type: catalog
---
# Documentation task catalog

Contributor framework notes, authoring references, examples, fixtures and runtime guides live here. The organizational baseline, team map, definition format and Role and Profile templates ship inside the package under `src/nplet/resources/` and are documented there as authoritative content, without any loader or activation. Select one task; follow its folder contract and load the named sections, not the whole tree. For edits, use the [maintenance contract](CONTEXT.md).

| Task | Start here | Scope to select |
| --- | --- | --- |
| Start or resume an issue, or execute an assigned step | [Contributor procedure](runtime/contributor-workflow.md) | Master planning, approval, step threads, handoffs and delivery; assigned children use the bounded step-thread route. |
| Create or update a pull request | [PR preparation and verification](runtime/contributor-workflow.md#pr-preparation-and-verification) | Title, exact repository template, evidence, current required checks and published read-back. |
| Set up a contributor checkout or local commit checks | [Runtime contract](runtime/CONTEXT.md#setup) | Environment setup, editor and check commands. |
| Diagnose pull-request checks or reproduce PR checks | [PR pre-merge checks](runtime/pr-checks.md) | Five checks, native suites, coverage, evidence and retries; branch enforcement remains a separate human-owned step. |
| Write or review repository tests | [Testing standards](runtime/testing.md) | Test boundaries, behavioral assertions, native cases, coverage obligations, evidence and exceptions. |
| Configure or inspect a native host hook | [Runtime contract](runtime/CONTEXT.md#host-protocol) | The selected host protocol and its limitations. |
| Change or use task-workspace lifecycle behavior | [Runtime contract](runtime/CONTEXT.md#lifecycle) | The affected contract and usage sections. |
| Change a shared framework rule or Role placement | [Framework contract](framework/CONTEXT.md) | The named rule in the packaged [baseline](../src/nplet/resources/organization/baseline.md), the team in the packaged [team map](../src/nplet/resources/organization/team-map.md) and the affected Role entry. |
| Author a Role or Profile | [Authoring contract](authoring/CONTEXT.md) | Assignment, selected Role entry, applicable rules, the packaged [definition format](../src/nplet/resources/templates/definition-format.md) and one packaged [template](../src/nplet/resources/templates/README.md). |
| Inspect or review a worked definition | [Example contract](examples/CONTEXT.md) | Exact candidate fields and corresponding criteria/rules. |
| Read or review a packaged Role definition | [Role definitions](../src/nplet/resources/roles/README.md) | The selected draft definition, its source and gap tables, and the packaged [review criteria](../src/nplet/resources/templates/definition-format.md#review-and-maintenance). |
| Walk through a governance or tooling scenario | [Fixture contract](fixtures/CONTEXT.md) | One fixture and its selected actors/rules. |

The [worked AGO example](examples/agent-governance-officer.md) is documentation, not a runtime resource. Keep it out of a fresh-author test packet. The packaged [baseline](../src/nplet/resources/organization/baseline.md) holds the shared organizational rules; the contributor [baseline](framework/baseline.md#selected-paper-fixtures-and-exclusions) defines paper-scope limits; the [publication rules](framework/baseline.md#authoring-and-publication-rules) retain the existing human PR check.
