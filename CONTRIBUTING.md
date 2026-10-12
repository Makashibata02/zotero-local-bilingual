# Contributing

Read [README.en.md](README.en.md) and [the build guide](docs/开发说明.md) before changing the plugin. Discuss substantial features in an issue first; include a reproducible example without a private paper or credentials.

Use Python 3.12.14, uv, Node 24, and the pnpm version in `plugin/package.json`. Install locked dependencies, then run backend tests with an isolated `ZLB_DATA_DIR`, frontend tests, and TypeScript checks. Use the built-in PDF or a generated fixture. Never require a contributor's paid API key for ordinary tests.

Keep source and artifact versions aligned. Service changes require an engine rebuild. UI-only changes still require a new XPI. See [current acceptance](docs/验收记录.md) for native UI checks that cannot be replaced by compilation.

Do not submit keys, `.env`, service tokens, databases, task caches, logs, Zotero profiles, user PDFs, or build artifacts. Sanitize screenshots. Before a pull request, run Gitleaks with redacted output and review the staged file list. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

Explain the problem, final behavior, tests, and remaining limitations in your pull request. Project contributions are licensed under AGPL-3.0-or-later; retain dependency notices. Binary releases are maintainer-reviewed, not automatic.
