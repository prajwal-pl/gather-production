"""Create a local org with demo members so the API is immediately usable.

    python -m gather.seed

Seeding users is a deliberate step: an org with no members will resolve every
commitment owner to "external", which looks like a bug but is correct.
"""

from __future__ import annotations

import sys

from .config import load_settings
from .db import Store
from .domain import task_fingerprint


def main(argv: list[str] | None = None) -> int:
    from datetime import date, timedelta

    settings = load_settings()
    store = Store(settings.db_path)
    org = store.ensure_org(settings.org_name)

    priya = store.add_user(org, "priya@acme.test", "Priya Raman")
    store.add_user(org, "marcus@acme.test", "Marcus Lee")

    store.upsert_connection(org, "fireflies", mode="org")
    store.upsert_connection(org, "gmail", mode="user", user_id=priya)

    title = "Send updated pricing deck to Northwind"
    if not store.find_task_by_fingerprint(org, task_fingerprint("priya@acme.test", title)):
        store.create_task(
            org,
            title=title,
            owner_user_id=priya,
            owner_email="priya@acme.test",
            due_date=(date.today() - timedelta(days=1)).isoformat(),
            fingerprint=task_fingerprint("priya@acme.test", title),
        )

    print(f"org: {org}")
    print(f"dir: {settings.data_dir}")
    print(f"db:  {settings.db_path}")
    print("\ntry:")
    print(
        f'  curl -X POST localhost:8000/webhooks/fireflies/{org} '
        '-H "content-type: application/json" '
        '-d \'{"event":"meeting.transcribed","meetingId":"ff_9f3a2c"}\''
    )
    print("  curl localhost:8000/proposals")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
