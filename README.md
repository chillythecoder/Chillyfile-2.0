# ChillyFile 2.0

A black-and-white file sharing starter with accounts, persistent file records, unique share links, search, deletion, and animated responsive UI.

## Run locally (Windows)

1. Install Python 3.11 or newer.
2. Open a terminal in this folder.
3. Run:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

4. Open http://127.0.0.1:5000

Local uploads are stored in `uploads/` and the SQLite database is `chillyfile.db`. Both survive app restarts. Back up both folders/files if you need a backup.

## Deploy on Render

1. Create a new GitHub repository named `ChillyFile2.0` and upload these files.
2. In Render, choose **New + → Blueprint** and connect the repository. Render reads `render.yaml`.
3. The blueprint creates a persistent disk mounted at `/var/data`. Uploads are saved in `/var/data/uploads`, not the temporary app directory.
4. Add a managed PostgreSQL database in Render and set the web service's `DATABASE_URL` to its Internal Database URL.
5. Confirm `SECRET_KEY` is a long random secret (Render can generate one).
6. Deploy. Test by creating an account, uploading a small file, copying its share link, and opening that link in a private browser window.
7. To verify persistence, redeploy and confirm the file still downloads.

## Important storage notes

- No Backblaze credentials are used or included.
- The browser never receives storage credentials. File writes happen on the Python server.
- For this starter, Render's persistent disk stores the actual uploaded bytes. It is not ephemeral storage.
- A persistent disk is attached to a paid Render service. If you use a host without persistent storage, configure durable object storage before accepting important uploads.
- The SQLite database is suitable for local development. For deployment, use PostgreSQL via `DATABASE_URL`.
- Do not commit `.env`, database files, or uploaded user files to GitHub.

## Current MVP

- Account creation and sign-in with hashed passwords
- Per-user file dashboard and search
- Multiple-file upload
- Unique public share page and direct download
- File deletion with ownership checks
- Animated hover states, drag-and-drop upload, theme toggle
- Server-side file storage and database records

This is a starter, not a finished production file-hosting service. Before opening it to the public, add email verification, password reset, rate limiting, malware scanning, quotas, abuse reporting, backups, and stronger operational monitoring.
