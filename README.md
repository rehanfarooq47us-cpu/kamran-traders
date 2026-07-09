# Kamran Traders

A Flask-based inventory, sales, purchases, ledger, and reporting app.

## Deployment Status

- Local git repository initialized and committed.
- Deployment files included:
  - `Procfile`
  - `runtime.txt`
  - `requirements.txt` with `gunicorn`
  - `.gitignore`
- App tests were checked and previously passed.

## Render Deployment Instructions

1. Push this repository to GitHub.
2. Create a new Web Service on Render.
3. Connect Render to the GitHub repository.
4. Configure the service:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Environment: Python 3.14
5. Deploy the service.
6. Render will provide a live URL once deployment completes.

## Local Run

```bash
python app.py
```

or using gunicorn:

```bash
gunicorn app:app
```
