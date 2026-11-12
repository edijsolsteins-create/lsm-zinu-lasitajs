# Vercel function: forwards to the shared handler in server.py (see vercel_app.py).
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from vercel_app import app  # noqa: E402,F401
