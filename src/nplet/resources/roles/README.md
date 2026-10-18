# Role definitions

This folder is reserved for Role definitions shipped inside the
`nplet` package. Definitions placed here are framework content shipped
inside the package; each definition records its own review status and
exact reviewed revision, and shipping grants no acceptance.

Each file records its own sources and open gaps, including the pending
reconciliation with the shared-permissions rules.

## Product Planning Team

- [Initiative Planner](initiative-planner.md): Initiative plan/roadmap for
  one product Project, from human-approved product intent or Company outcome.
- [Epic Planner](epic-planner.md): Epic plan with Milestone decomposition,
  from the approved Initiative roadmap.
- [Issue Planner](issue-planner.md): Work Item Plan for one Work Item, from
  the approved Epic plan and its Milestone.
- [Plan Reviewer](plan-reviewer.md): independent, read-only review of
  Initiative, Epic/Milestone and Work Item planning proposals and
  Controller-generated Wave proposals.

No loader or activation mechanism reads this folder. Publishing the package,
installing it, or adding a file here does not activate governance and does
not grant any authority to any agent, Role or person.
