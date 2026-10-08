#!/bin/zsh
cd "$(dirname "$0")"
: "${DOCKER_HOST_FQDN:?set DOCKER_HOST_FQDN to the docker host running tiqora-ai-worker}"
esc() { sed "s/'/'\\\\''/g" "$1"; }
/usr/local/bin/knock-py -d 20 -u $DOCKER_HOST_FQDN 8472 65129 2038 && \
rtk proxy ssh root@$DOCKER_HOST_FQDN "docker exec -i -e SKIP_PROD=1 -e VARIANTS_JSON='$(esc $1)' -e CASES_JSON='$(esc cases.json)' tiqora-ai-worker sh -c 'cat > /tmp/sim.py && cd /app && /app/.venv/bin/python /tmp/sim.py ${3:-3} 2>&1 | grep -E \"^(#|\\{)\"'" < simulate_triage.py >| $2 2>&1
