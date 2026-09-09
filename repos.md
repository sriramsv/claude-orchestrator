# Repo registry

Routing table for `orchestrate` dispatch decisions. Add a row whenever a new
repo becomes a regular target; keep the one-liner focused on *what kind of
task belongs here*, not a general description of the project.

| Repo | Path | Route here for |
|---|---|---|
| IAC | ~/Projects/IAC | k3s/Flux manifests, Talos, OpenTofu (Proxmox/Cloudflare), SOPS secrets, Ansible, DNS, Proxmox cluster ops |
| homeassistant-config | ~/Projects/homeassistant-config | HA automations, scripts, scenes, dashboards, Zigbee2MQTT config. Commit-only — never push live changes to the running pod. |

Not listed here yet: everything else under ~/Projects (blog, graph-crm,
esphome, obsidian, etc). If a task doesn't clearly match a row above, don't
guess — check the target repo's own CLAUDE.md/README, or ask the user which
repo(s) the task belongs to.
