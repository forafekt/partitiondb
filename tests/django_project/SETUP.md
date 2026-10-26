# Django integration fixture

This project is the runnable integration application and test migration graph.
Library defaults own provisioning/forms/commands; its models, memberships and
workspace seed/verification hook demonstrate application customization.
Keep its migration history intact. Its settings contain local fixture credentials.

From the repository root:

```sh
docker compose up --build -d
```

Follow the [local application guide](../../docs/LOCAL_INFRASTRUCTURE.md) for tenant
creation, host login, worker checks and OpenBao UI access. For disposable automated
validation use [testing](../../docs/TESTING.md). For a consuming application start
with [settings](../../docs/SETTINGS.md) and [Django integration](../../docs/DJANGO.md).
