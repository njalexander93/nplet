---
doc_type: maintenance-contract
---
# Runtime and contributor guides

Select one route. Commands retain their documented repository-root working directory; moving a guide does not change command execution paths.

## Inputs

Working inputs are the selected checkout, host/event or lifecycle operation and the exact observed behavior or proposed change. Reference inputs are below; load only the selected route.

### Contributor workflow

Research, inspection, planning, administration and ordinary notes use the [effect boundary](task-workspace.md#task-free-work-and-implementation-effects) without task setup. Before implementation, or when starting or resuming an issue, follow the [canonical contributor procedure](contributor-workflow.md). An assigned child uses its [step-thread route](contributor-workflow.md#step-thread-route). This procedure owns contributor sequencing and approval; the lifecycle references below own storage and readiness mechanics.

### Setup

Read [development.md](development.md): environment setup, local commit checks, editor setup and checks. Follow the host guide only when configuring host hooks.

### Host protocol

Read the selected host row in [native protocols](host-hooks.md#native-protocols-and-failure-semantics), its applicable [setup](host-hooks.md#setup-and-bootstrap), [coverage](host-hooks.md#implemented-coverage) and [validation limits](host-hooks.md#validation-and-remaining-evidence), plus [host limitations](task-workspace-usage.md#host-limitations). For admission/blocking behavior, also read [host adapters](task-workspace.md#host-adapters) and [bootstrap exceptions](task-workspace.md#bootstrap-exceptions). Use the applicable cited public host protocol, not another host's payload shape.

### Lifecycle

Read the affected [core interface/state](task-workspace.md#core-interface-and-state), [ownership/context](task-workspace.md#ownership-context-and-participant-lifetime), [transaction](task-workspace.md#coordination-and-crash-recovery), [event](task-workspace.md#events-and-retention) or [archive/recovery](task-workspace.md#archive-provider-boundary-and-recovery) section. Pair it with the matching [usage](task-workspace-usage.md#usage) subsection and [recovery semantics](task-workspace-usage.md#recovery-and-completion-semantics). Read [storage/identity](task-workspace.md#storage-and-identity) when paths, registration or bindings are affected.

## Process

1. Select the operation/host and its exact reference sections. For implementation questions, inspect the named source/config/tests rather than inferring behavior from this layout.
2. Follow the authorized task using the applicable usage or contract; preserve stated platform, trust, provider and implementation limits.
3. For documentation changes, update the affected contract/usage links and report validation appropriate to the change.

## Outputs

The requested setup/diagnostic result, or scoped guide changes with evidence. This folder stores documentation, not live task state.

## Human check

For document changes use the existing [PR check](../CONTEXT.md#human-check), checking command paths and implementation claims. Operational actions retain their existing authorization boundaries.
