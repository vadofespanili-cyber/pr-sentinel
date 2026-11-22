# Base image pinned to a specific patch release AND its digest (verified
# against Docker Hub), so rebuilds are reproducible. Update the tag and
# the digest together when moving to a newer Python patch release.
FROM python:3.12.16-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# The container runs as root on purpose: GitHub Docker actions need write
# access to the checked-out workspace, which is owned by the runner user.
ENTRYPOINT ["/entrypoint.sh"]
