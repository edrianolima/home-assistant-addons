ARG BUILD_FROM
FROM $BUILD_FROM

# jq reads the Supervisor's /data/options.json. bashio is deliberately absent:
# it lives in the Home Assistant base images, and this one is upstream's.
# The base is nginx:alpine, so apk applies.
USER root
RUN apk add --no-cache jq

COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Replace the entrypoint, not the command: run.sh translates add-on options into
# the environment upstream expects and then exec's the original entrypoint,
# which ends in `exec "$@"` and hands control to the s6 tree in CMD.
ENTRYPOINT ["/run.sh"]
CMD ["/init"]
