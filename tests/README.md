# Integration / golden-master tests

Cross-package tests live under `tests/golden/` (real OTRS/Znuny peer container
vs Tiqora on shared MariaDB). Multi-peer matrix: see
[`docs/support-matrix.md`](../docs/support-matrix.md) and
[`golden/README-multi-peer.md`](./golden/README-multi-peer.md).

```sh
just golden-peers-ready
GOLDEN_PEER=znuny-6.5 just golden-run
just golden-all-peers   # sequential, all peers with source on disk
```

Unit / Layer-A schema-matrix tests for the backend package are under
`backend/tests/` (`-m schema_matrix`).
