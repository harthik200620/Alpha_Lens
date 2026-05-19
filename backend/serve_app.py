import os
import sys
from wsgiref.simple_server import make_server

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("ALPHA_LENS_SKIP_AUTO_REPAIR", "1")

from app import app, start_background_workers


if __name__ == "__main__":
    start_background_workers()
    with make_server("127.0.0.1", int(os.environ.get("PORT", "5000")), app) as httpd:
        print("Alpha Lens server running on http://127.0.0.1:5000", flush=True)
        httpd.serve_forever()
