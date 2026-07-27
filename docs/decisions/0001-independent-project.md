# ADR 0001: Matterport Ops is an independent project

**Status:** Accepted

Matterport Ops owns its repository, `app` package, assets, configuration, tests,
documentation, and `mpops.db` SQLite database. Phoenix Database may be consulted as
a reference implementation, but copied behavior must be adapted and documented.
Matterport Ops must never import Phoenix modules or connect to `phoenix.db`.

