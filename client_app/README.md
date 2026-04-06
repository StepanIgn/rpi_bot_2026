# TCP Camera Client (PyQt5) — Qt Creator UI (.ui)

Compatible with your server:
- Video framed: :9000  (VSTR header + H.264 Annex-B NAL payload)
- Control: :9001 (JSON lines)
- Debug raw H.264: :9002 (ffplay)

## Install
```bash
cd client_app
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
python run_client.py
```

## Edit UI in Qt Creator
Open `ui_mainwindow.ui` in Qt Creator Designer, edit and save.
The application loads this file at runtime via `PyQt5.uic.loadUi`, so you don't need to run `pyuic5`.
