# One-shot container that creates the pipeline topics, then exits.
FROM apache/kafka:4.3.2
COPY create-topics.sh /create-topics.sh
ENTRYPOINT ["/bin/bash", "/create-topics.sh"]
