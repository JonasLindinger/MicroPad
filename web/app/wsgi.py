"""MicroPad Config Generator - web backend."""
import os
from app import app, DATA_FILE  # noqa

# gunicorn entry point: from this file run the flask app factory
if __name__ == "__main__":
    port = int(os.environ.get("MICROPAD_PORT", "8080"))
    app.run(host="0.0.0.0", port=port)