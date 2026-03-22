---
name: up
description: Bring up the local development stack
allowed-tools: Bash
---

Bring up the local development stack with all profiles:

```bash
docker compose -f local.yml --profile planka --profile workers up
```
