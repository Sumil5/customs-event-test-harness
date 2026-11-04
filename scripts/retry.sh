#!/bin/sh
# Retry a network-bound command, such as an image build that downloads from PyPI.
# CI uses it for builds and pulls only. Tests are never retried, so a flaky test can't hide.
#
#   sh scripts/retry.sh 3 docker compose --profile test build
set -u
attempts=$1
shift
delay=${RETRY_DELAY:-20}
n=1
until "$@"; do
  if [ "$n" -ge "$attempts" ]; then
    echo "retry.sh: giving up after $attempts attempts: $*" >&2
    exit 1
  fi
  echo "retry.sh: attempt $n of $attempts failed; retrying in ${delay}s: $*" >&2
  n=$((n + 1))
  sleep "$delay"
done
