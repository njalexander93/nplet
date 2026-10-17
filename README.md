# nplet.ai

**A modular framework for running an agent-powered engineering fleet.**

The Nplet framework brings organizational structure to AI work: specialized roles, shared rules, clear responsibilities and coordinated handoffs. The goal is to help people build teams of agents that work toward a common purpose while humans retain direction and approval of consequential decisions.

## What we're building

- **Teams with clear responsibilities.** Define what each agent owns, what it needs and when to hand work to another role.
- **Shared context and continuity.** Keep plans, decisions and task progress available as work moves between agents and sessions.
- **Review and human oversight.** Separate implementation from independent review, with explicit human decisions at key boundaries.
- **A reusable foundation.** Give companies a common structure they can adapt to their own work and operating rules.

## Current development

The Nplet framework is in **early development**. This repository currently contains the framework's published rules, role and profile templates, worked examples, and Python tooling for shared local task workspaces.

The workspace tooling includes integrations for Codex, Claude Code and Cursor. The broader company runtime and complete set of executable roles are still being developed.

## Requirements and setup

Contributor setup supports **macOS, Linux and native Windows**. The setup guides cover Python, Poetry, editor configuration and local checks, including platform requirements.

1. **[Set up your development workspace](docs/runtime/development.md#set-up-each-worktree)**
2. **[Configure your agent's task-workspace integration](docs/runtime/host-hooks.md#setup-and-bootstrap)**
3. **[Use the shared task workspace](docs/runtime/task-workspace-usage.md#usage)**

Research, inspection, planning, administration and ordinary notes can proceed without task setup. Before implementation, use the [contributor procedure](docs/runtime/contributor-workflow.md) and its human roadmap approval gate.

For contributions, follow the [local checks](docs/runtime/development.md#checks) and submit a pull request for human review.

## Explore the framework

- [Organization and teams](src/nplet/resources/organization/team-map.md)
- [Role and profile definitions](src/nplet/resources/templates/definition-format.md)
- [Documentation index](docs/README.md)

## License

This repository uses the [PolyForm Noncommercial License 1.0.0](LICENSE).
