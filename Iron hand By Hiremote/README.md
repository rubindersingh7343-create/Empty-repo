# Hiremote Operations Portal

A lightweight Flask + SQLite application that powers the Hiremote / Iron Hand workflow:

- **Employees** upload end-of-shift packs (scratcher video, cash photo, sales photo + notes).
- **Store Managers** push daily/weekly/monthly reports with attachments.
- **Clients** log in securely to view every upload for their store with filtering, previews, and downloads.

The UI is mobile-friendly and branded with the Iron Hand palette so it can be deployed quickly as a standalone portal or behind an existing SSO layer.

## Getting started

```bash
cd "Iron Hand App/Iron hand By Hiremote"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000 and sign in with one of the seeded accounts:

| Role | Email | Password | Notes |
| --- | --- | --- | --- |
| Employee | `employee@hiremote.com` | `password123` | Upload end-of-shift packet |
| Store Manager | `ironhand@hiremote.com` | `operations123` | Upload daily/weekly/monthly reports |
| Client | `client@hiremote.com` | `clientaccess` | Read-only access scoped to Store 101 |

The SQLite database lives under `instance/hiremote.db` and is created automatically the first time the server starts. Uploaded files are stored in `storage/uploads/<timestamp>/`.

## Features

- Role-based authentication with per-store scoping for clients.
- Required media uploads for end-of-shift submissions, with auto timestamps and notes.
- Store Manager reporting interface covering daily recaps, weekly orders, and monthly packages.
- Client dashboard with filters (store, employee, category, date range), inline previews (image/video), and download links for all file types.
- Secure download endpoint that prevents path traversal and keeps files behind login.
- Responsive, branded UI with helpful microcopy for employees and Store Managers.

## Customization

- Update the seeded users or add new ones inside `app.py` → `DEFAULT_USERS`.
- To plug in a real identity provider, replace the login logic in `/login` with your SSO callback.
- Swap the local file system storage in `save_uploaded_files` with an S3/Azure/GCS client. Only that helper and the `/files/<path>` route need to change.
- Brand colors can be tweaked in `static/css/style.css`.

## Next steps

- Replace demo secrets by setting `HIREMOTE_SECRET` in your environment.
- Add email/SMS alerts on new submissions with a background worker.
- Push files to cloud storage and serve through signed URLs for high availability.
- Layer on more granular permissions (multi-store clients, per-store reporting Store Managers, etc.).

## Testing

Because this is a small Flask app, manual verification is straightforward:

1. Start the server and sign in as each role.
2. Complete the employee upload form and confirm a success flash + new card.
3. Send a daily/weekly/monthly report as the Store Manager user.
4. Sign in as the client and verify filtering + file previews, and that you only see your store.

For automated testing you can layer on [pytest](https://docs.pytest.org/) with Flask's test client—see `app.test_client()` for hooks. Let me know if you'd like the initial test suite scaffolded.
