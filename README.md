3drtma-viewer — minimal RTMA / 3DRTMA web map viewer

Quick start (dev):

1. Create and activate a venv (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Note: `cfgrib` requires the ECMWF ecCodes system library installed on your OS. On Windows you can use conda:

```powershell
conda install -c conda-forge eccodes cfgrib
```

3. Run the app:

```powershell
$env:FLASK_SECRET = 'dev-secret'
python .\app.py
```

4. Open http://localhost:5000

Notes:
- The app lazily loads heavy data libraries; if you see errors on generator creation, ensure dependencies (`cfgrib`, `xarray`, etc.) are installed.
- For production, run under a WSGI server and set a persistent `FLASK_SECRET`.
