# Security and privacy

## Data handling

The service listens on loopback and requires a local service token. Model API keys are stored in Windows Credential Manager, not source files. User settings, jobs, usage audits, outputs, and runtime caches live outside the repository under `%LOCALAPPDATA%\ZoteroLocalBilingual`.

Document text is sent to the selected provider for translation. Do not use a remote provider for sensitive documents unless its data policy is acceptable to you. The engine bundle is verified by size and SHA-256 before activation.

## Reporting

Use the repository's **Security → Report a vulnerability** if private reporting is available. Do not post exploitable details, keys, local service tokens, user PDFs, or full databases in public issues. If private reporting is unavailable, open a minimal issue asking for a private contact channel without disclosing the vulnerability.

Include the plugin/runtime version, a sanitized reproduction using a generated PDF, expected behavior, and impact. API key values are never needed. If a key was exposed, revoke it with the provider; deleting a visible file does not remove it from Git history.

The current maintained version is 1.0.0. Source, Git history, and release archives are checked before publication. Engine downloads are anonymous and do not include browser credentials or a GitHub token.
