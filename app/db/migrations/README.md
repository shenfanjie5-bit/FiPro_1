# Migrations (Alembic)

## Commands

- Upgrade to latest:
```bash
alembic upgrade head
```

- Show current revision:
```bash
alembic current
```

- Show history:
```bash
alembic history
```

## Baseline

- Initial revision: `20260210_0001_m1_contract_baseline.py`
- Scope: M1 contract-aligned DB structure for schema/API/replay fields.

## Notes

- Keep SQL changes versioned through Alembic revisions.
- `sql/001_init.sql` is retained as reference/bootstrap, not the primary forward path.
