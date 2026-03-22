---
name: down
description: Tear down the local development stack
allowed-tools: Bash
---

Tear down the local development stack:

```bash
docker compose -f local.yml --profile planka --profile workers down
```
