# Zabbix template for Tiqora

**Status:** placeholder — template not yet authored.

## Planned coverage

| Check | Method | Target |
|---|---|---|
| API liveness | HTTP agent | `GET /health` |
| API readiness | HTTP agent | `GET /ready` |
| Prometheus metrics | HTTP agent / Prometheus scrape | `GET /metrics` |
| Request latency | Metric items | `tiqora_http_request_duration_seconds` |
| Worker queue depth | Metric items | (taskiq metrics — TBD) |
| Poller lag | Metric items | (Znuny write poller) |
| Mail queue errors | Metric items | |

## Layout (forthcoming)

```
deploy/zabbix/
  README.md                 # this file
  tiqora_template.yaml      # Zabbix 6.0/7.0 export
  screens/                  # optional dashboards
```

## Notes

- Prefer scraping `/metrics` where Prometheus integration is available; the
  Zabbix template will use HTTP agent JSON or Prometheus pattern items for
  environments without Prometheus.
- During parallel operation, continue monitoring Znuny with its existing
  checks; Tiqora templates are additive.
