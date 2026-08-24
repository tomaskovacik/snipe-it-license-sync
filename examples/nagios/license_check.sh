#!/bin/bash
# Example Nagios check_command wrapper for snipe-it-license-sync.
#
# SECURITY NOTE: pulling ":latest" here is convenient but unsafe for
# production — it means a compromised or malicious image published to that
# tag would run immediately with access to your mounted .env secrets
# (--env-file). Pin to an exact version *and* its content digest instead,
# e.g.:
#
#   ghcr.io/tomaskovacik/snipe-it-license-sync:v0.3.5@sha256:<digest>
#
# and use a tool like Renovate to propose the bump, with a
# `minimumReleaseAge` deferral (e.g. 14 days) before it's applied — that
# gives a compromised release time to be caught and pulled before you'd
# ever actually run it against your credentials.
docker pull -q ghcr.io/tomaskovacik/snipe-it-license-sync:latest > /dev/null
docker run --name snipe-it-license-check --rm --env-file /etc/snipe-it-sync/.env \
	-e NAGIOS=true \
	ghcr.io/tomaskovacik/snipe-it-license-sync:latest
exit $?
