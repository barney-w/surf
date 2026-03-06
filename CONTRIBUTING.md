# Contributing to surf

Thank you for your interest in contributing to surf! This guide covers the development setup, coding standards, and PR process.

---

## Development Setup

### Prerequisites

- **Python** >= 3.12
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- **[just](https://github.com/casey/just)** for task running
- **[Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)** for deployment

### Getting Started

```bash
git clone https://github.com/barney-w/surf.git
cd surf
cd api && uv sync && cd ../ingestion && uv sync && cd ..
just dev
```

### Common Commands

| Command | Description |
| --- | --- |
| `just dev` | Run API with hot reload (port 8090) |
| `just devui` | Launch DevUI -- interactive agent chat (port 8091) |
| `just test` | Run API tests |
| `just test-ingestion` | Run ingestion tests |
| `just lint` | Lint all code |
| `just typecheck` | Type-check all code |
| `just format` | Format all code |
| `just setup-dev` | Deploy dev Azure resources + generate .env |
| `just teardown-dev` | Delete dev Azure resources |

---

## Pull Request Guidelines

1. **Branch from `main`** -- create a feature branch with a descriptive name (e.g., `feat/new-agent`, `fix/stream-timeout`).
2. **Keep PRs focused** -- one feature or fix per PR. Smaller PRs are reviewed faster.
3. **Write tests** -- new features should have unit tests. Bug fixes should include a regression test where practical.
4. **Pass CI** -- make sure `just lint`, `just typecheck`, and `just test` all pass before requesting review.
5. **Update docs** -- if you add or modify an agent, endpoint, or infrastructure module, update the relevant documentation.

---

## Coding Standards

### Python

- Strict type checking is enforced via **pyright** across all packages.
- Use **Pydantic 2** models for all request/response schemas.
- Follow the existing patterns in `src/agents/` for new domain agents.

### Linting and Formatting

- **ruff** is used for both linting and formatting.
- Run `just lint` and `just format` before committing.

### Testing

- Test framework: **pytest**
- API tests live in `api/tests/unit/` and `api/tests/integration/`.
- Ingestion tests live in `ingestion/tests/`.
- Aim for meaningful assertions that test behaviour, not implementation details.

### Adding a New Agent

1. Subclass `DomainAgent` in `src/agents/`.
2. Define the agent's domain, RAG document types, and system prompt.
3. The agent is auto-registered via `__init_subclass__` -- no manual wiring needed.
4. Add tests and update documentation.
5. See the runbook: [Adding a new agent](./docs/runbooks/add-new-agent.md).

---

## Questions?

Open an issue or start a discussion on GitHub. We are happy to help!
