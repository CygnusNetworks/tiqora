# Security policy

Tiqora is a helpdesk: it holds customer mail, attachments and credentials for mail
servers and LLM providers. We take reports seriously and are grateful for them.

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a security
problem.**

Report it privately through GitHub:
[**Report a vulnerability**](https://github.com/CygnusNetworks/tiqora/security/advisories/new)
(Security tab → *Report a vulnerability*). Only the maintainers can see the report.

Please include:

- the Tiqora version (shown under *Admin → System info*, or the image tag),
- the affected component (API, MCP server, worker, web UI, GenericInterface layer),
- steps to reproduce or a proof of concept, and the impact you expect,
- whether Tiqora runs in parallel operation with Znuny/OTRS, and which version.

Use made-up data in your report; do not send us real customer content.

## What to expect

- We acknowledge your report within **3 working days**.
- We tell you our assessment and a planned fix date within **10 working days**.
- We publish a fixed release and a GitHub security advisory, and credit you unless you
  prefer to stay anonymous.

## Supported versions

Tiqora is pre-1.0. Security fixes go into the **latest release** only; please upgrade
to the newest `v0.x` tag before reporting.

## Scope

In scope: the code in this repository and the published container image
`ghcr.io/cygnusnetworks/tiqora`.

Out of scope: Znuny/OTRS itself (report those upstream), the evaluation setup in
`docker-compose.quickstart.yml` (its fixed passwords are intentional), the mock-data
demo on GitHub Pages, and findings that need an already compromised host or admin
account.
