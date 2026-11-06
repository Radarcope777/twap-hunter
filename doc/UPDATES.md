# Updates

1. Stop the process (Ctrl+C, systemd, Task Scheduler, or kill the port owner).
2. Keep `.env` and `data/` (database + `.encryption_key`).
3. Replace application files (git pull on `fix/v2` or unpack a new tree **over** the code, not over `data/`).
4. `.venv/.../python -m pip install -r requirements.txt`
5. Start from the project root (`python -m backend` or uvicorn).
6. Confirm `GET /health` and that paper/real bots still list.

## Notes

- UI cache-bust query on some real pages (`style.css?v=...`) — hard-refresh if the layout looks stale.
- SQLite schema is created/migrated in `backend/storage.py` on open. Do not copy a DB from a newer build onto an older binary without reading storage migrations.
- Railway: Dockerfile copies only `backend/` and `frontend/`. Docs and `data/` are not in the image. Set `DATA_DIR` to a volume.
- `BOOT_ID` changes on process start; the UI may auto-reload when it changes.

## Rollback

Stop, restore the previous code directory, keep the same `data/` only if the older code can read that schema. If unsure, restore a DB backup taken **before** the upgrade.
