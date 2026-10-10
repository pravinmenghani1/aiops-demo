# build-agent-07: a real Jenkins SSH build agent with a small artifact disk and a narrow ops API.
FROM jenkins/ssh-agent:latest-jdk21
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pytest git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*' \
 && JAVA_BIN="$(command -v java || echo "$JAVA_HOME/bin/java")" \
 && ln -sf "$JAVA_BIN" /usr/local/bin/java
COPY ops_api.py seed_artifacts.py /opt/agentops/
COPY entrypoint.sh /usr/local/bin/agentops-entrypoint
# Strip Windows line endings in case the kit was checked out on Windows with core.autocrlf=true
RUN sed -i 's/\r$//' /usr/local/bin/agentops-entrypoint && chmod +x /usr/local/bin/agentops-entrypoint
EXPOSE 22 8700
ENTRYPOINT ["/usr/local/bin/agentops-entrypoint"]
