@echo off
setlocal

if not exist .venv (
  python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if not exist .env (
  echo Please copy .env.example to .env and set DEEPSEEK_API_KEY before translating.
)

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
