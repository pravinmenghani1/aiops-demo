#!/bin/sh
# Start the ops API next to sshd. The artifact disk is a size-limited tmpfs mounted at /srv/artifacts.
set -e
mkdir -p /srv/artifacts
chmod 1777 /srv/artifacts
python3 /opt/agentops/ops_api.py &
# setup-sshd copies the container's environment into every SSH session, so every Jenkins build
# would see the ops token. Only the ops API needs it.
unset OPS_TOKEN
exec setup-sshd "$@"
