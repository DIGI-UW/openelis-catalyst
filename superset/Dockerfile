# Superset renders Catalyst's saved queries against the same Spark source the
# Gateway queried. The published image ships pyhive but not the Thrift stack
# its hive:// dialect needs, so the driver is added here rather than left to a
# runtime pip install that would differ between the server and the importer.
#
# FROM is the same pinned digest the compose files name, so adding the driver
# does not change which Superset this is.
ARG SUPERSET_IMAGE=apache/superset:6.1.0-dev@sha256:5822dff49c41fd745ce33e38af502f9c64df30d133aeba148c5d89b35a1004ef
FROM ${SUPERSET_IMAGE}

# hive_pure_sasl, not hive: the latter pulls the C sasl package, which needs a
# compiler the Superset image does not carry. The thriftserver speaks SASL
# PLAIN, which pure-sasl implements.
ARG SUPERSET_DRIVER_REVISION=pyhive[hive_pure_sasl]==0.7.0
USER root
RUN /app/.venv/bin/pip install --no-cache-dir "${SUPERSET_DRIVER_REVISION}"
USER superset
