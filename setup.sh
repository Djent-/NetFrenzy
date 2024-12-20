#!/bin/bash

# Install our own requirements
python3 -m pip install -r requirements.txt

sudo apt install neo4j tshark -y
sudo neo4j start

sudo mkdir -p /usr/share/neo4j/plugins
# Download the graph data library from neo4j
# Install the graph data library into the plugins folder
sudo wget https://github.com/neo4j/graph-data-science/releases/download/2.1.6/neo4j-graph-data-science-2.1.6.jar -O /usr/share/neo4j/plugins/neo4j-graph-data-science-2.1.6.jar

# Change config
echo "dbms.security.procedures.unrestricted=gds.*" | sudo tee -a /etc/neo4j/neo4j.conf
