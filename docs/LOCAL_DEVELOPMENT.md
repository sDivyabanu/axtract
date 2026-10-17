# Local-only AXTRACT

AXTRACT no longer includes an account system or cloud authentication/storage integration.
It runs only after explicit local opt-in. The old protected deployment mode has been
retired at the owner's request; disabling local mode **denies access**, not anonymous
network access. A future network deployment needs a new authentication design.

## Start (PowerShell, two terminals)

Backend, from the repository root:

```powershell
$env:AXTRACT_LOCAL_NO_AUTH = "true"
backend\.venv\Scripts\python.exe backend\serve_local.py
```

Frontend:

```powershell
cd frontend
$env:AXTRACT_LOCAL_NO_AUTH = "true"
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev:local
```

Open **http://127.0.0.1:3000**. The flow is:

**Landing → Start Extracting → Upload → Extract & Verify → View/Download Results**.

The sidebar exposes document history and Smart Benchmark without account controls.
The old `/login` bookmark redirects to `/`. No real accounts or tokens are created.
The document parser, Verify, results and benchmark runner are reused.

Alternatively put the opt-in flag in `backend/.env` and `frontend/.env.local`;
examples default to false. Existing secret environment files are not modified or read
for account access. You can remove their obsolete cloud settings yourself.

## Listening boundary

- Backend `serve_local.py` binds an actual listening socket to `127.0.0.1` (default)
  or `::1` (`--host ::1`). The application verifies that socket at startup and on requests.
- Frontend `dev:local` binds before loading Next config. Config verifies the actual
  listener. `AXTRACT_LOCAL_HOST=::1` selects IPv6; public/wildcard binds are refused.
- Setting the flag while running ordinary `uvicorn main:app` or `next dev/start`
  is insufficient and fails closed. There is no public deployment/build workflow.
- Remote peers, non-local Host/Origin, cross-site browser requests and forwarded-header
  proxy claims are rejected. Proxy header trust is disabled. Do not use tunnels,
  reverse proxies, port forwarding or shared/untrusted OS accounts with this mode.
- Loopback access is not per-person authorization: other processes/users on the same
  machine may access the app. Keep the data directory private using your OS permissions.
- Normal development binds are never inferred from a client-controlled header or an
  environment variable claiming that a public listener is local.

## Persistence and isolation

The isolated identity is `axtract-local-development`, not a real account UUID.
Document metadata, outputs and processing history use SQLite at
`backend/data/local-no-auth/documents.sqlite3`. Encrypted originals use the adjacent
`objects/` folder. A separate generated `master.key` wraps each document key.
The old cloud key is not reused. Back up this whole directory together; losing the key
means losing access to encrypted originals. Never commit it.

`AXTRACT_LOCAL_DATA_DIR` optionally changes the base directory; `local-no-auth/` is
always appended to maintain the separate namespace. Real-user identities and storage
paths are rejected. No cloud data is migrated, overwritten or deleted.

New temporary uploads and document previews use `backend/uploads/local-no-auth/`,
separate from caches of the retired account flow. Existing caches are not deleted.
DealLens retains its existing local data directory. Smart Benchmark keeps
its existing workspace (`benchmark_runs/` or `AXTRACT_BENCH_WORKSPACE`), results, logs
and history unchanged. No benchmark was launched or historical output rewritten by
this change. Use an explicit warning if storage fails and parsing falls back to an
unsaved result; successful local saves remain available after restart.

## Checks

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_local_mode.py backend/tests/test_documents_api.py backend/tests/test_api.py backend/benchmark/tests -q
cd frontend
npm test
npx tsc --noEmit
npm run lint
```

Tests use temporary storage, socket boundary checks and fake benchmark workers. They
do not create accounts or run the official corpus. Ordinary backend startup refuses
to start when the flag is absent/false or the actual loopback socket is not supplied.
