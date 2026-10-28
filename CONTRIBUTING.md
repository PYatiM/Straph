# Contributing
- Trunk-based: `main` is protected. Branch from `main`, keep branches under three days, open a PR early.
- Branch names: `<type>/<owner>/<task-id>-<slug>`, for example `feat/mohit/T15-detector-v1`.
- Commits: Conventional Commits (see .gitmessage). One logical change per commit. Reference the task id.
- PRs: use the template; one reviewer minimum; two when /contracts changes. Squash-merge.
- Contracts first: change /contracts in its own PR and tag every owner before dependent code lands.
- Never commit secrets, tokens, real user data or raw traffic captures.
Setup: `git config commit.template .gitmessage`, `pip install pre-commit ruff pytest jsonschema`, `pre-commit install`.
