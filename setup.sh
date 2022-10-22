#!/bin/bash

# Install our own requirements
python3 -m pip install -r requirements.txt

# Download the graph data library from neo4j
# Install the graph data library into the plugins folder
wget https://github.com/neo4j/graph-data-science/releases/download/2.1.6/neo4j-graph-data-science-2.1.6.jar -O /var/lib/neo4j/plugins/neo4j-graph-data-science-2.1.6.jar
sudo chown neo4j:adm /var/lib/neo4j/plugins/neo4j-graph-data-science-1.8.3.jar

# Change config
echo "dbms.security.procedures.unrestricted=gds.*" | sudo tee -a /etc/neo4j/neo4j.conf

# Restart neo4j
sudo systemctl restart neo4j
