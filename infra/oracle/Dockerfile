# Oracle AI Database 26ai Free (23.26) with the CUSTOMS schema baked in.
# Baking the init scripts (rather than bind-mounting them) keeps the stack
# working under Docker-in-Docker in CI, where host paths are not visible.
FROM gvenzl/oracle-free:23.26.3-slim-faststart
COPY init/ /container-entrypoint-initdb.d/
