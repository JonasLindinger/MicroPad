# Container image for the MicroPad configurator.
#
# The API is fail-closed: gunicorn binds loopback only unless the admin secret is set.
# For LAN/browser access from outside the container provide both and publish 8080:
#   docker run --rm -p 8080:8080 \
#     -e MICROPAD_ADMIN_SECRET=<secret> \
#     -e MICROPAD_BIND=0.0.0.0:8080 \
#     -v "$PWD/data:/app/data" micropad-configurator
# Never bake a real secret into this image.
#
# A trusted-LAN deployment that deliberately serves without a secret is opt-in:
#   -e MICROPAD_ALLOW_UNAUTHENTICATED_LAN=1 -e MICROPAD_BIND=0.0.0.0:8080
# It logs a warning at every start; see docs/deployment.md.

# Stage 1: build the host simulator from the *shipped* firmware core. The configurator
# renders the panel preview and derives the byte budgets from this binary, so an image
# without it starts fine and then answers 503 on /api/simulate with a build path in the
# error - a "broken preview" that is really a packaging gap. Plain g++ is enough: the
# core is dependency-free by design (see docs/firmware.md).
FROM python:3.12-slim AS simulator
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY firmware/ ./firmware/
COPY tools/ ./tools/
RUN g++ -std=c++17 -Wall -Wextra -Werror -pedantic -Ifirmware \
        tools/micropad_sim.cpp firmware/micropad_core.cpp -o /build/micropad_sim

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

# The simulator is looked up at <repo root>/build/host/micropad_sim, which is /app here.
COPY --from=simulator /build/micropad_sim /app/build/host/micropad_sim

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "micropad.wsgi:app"]
