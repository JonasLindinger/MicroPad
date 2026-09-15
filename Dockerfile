# Container image for the MicroPad configurator.
#
# The API is fail-closed: gunicorn binds loopback only unless the admin secret is set.
# For LAN/browser access from outside the container provide both and publish 8080:
#   docker run --rm -p 8080:8080 \
#     -e MICROPAD_ADMIN_SECRET=<secret> \
#     -e MICROPAD_BIND=0.0.0.0:8080 \
#     -v "$PWD/data:/app/data" micropad-configurator
# Never bake a real secret into this image.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# contracts/ is part of the runtime: the configurator derives its item types, actions,
# key ids, topic names and payload ceilings from contracts/mqtt-contract.json at import
# time. Leaving it out produces an image that starts and then fails on the first request,
# which is why tests/test_deployment_assets.py pins this line.
COPY src/ ./src/
COPY contracts/ ./contracts/
COPY gunicorn.conf.py ./

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "micropad.wsgi:app"]
