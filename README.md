# wireshark4j
Import a pcap file into Neo4j and view the network graph

# Usage

```bash
python3 main.py -p ../path/to/your.pcap -c config.json -i 00:50:56:e5:33:52
```

Processes approx. 40-60 packets per second into Neo4j.

# Why

 - Visualize the network from a PCAP
 - Verify network segmentation
 - Identify CTF players attacking each other

# Example and how to resize

## Using the custom Neovis.js client

Found at `/web/index.html`, just open it in your browser

I made this

 - Config and Query UI can be hidden with a toggle
 - Query bar has a history. Use up/down arrow. Clears upon page reload
 - Pause button halts the graph movement physics

![Preview](/screenshots/Screen%20Shot%202022-01-19%20at%203.02.39%20PM.png "Neovis.js client")

## Using the Neo4j Browser

![Preview](/screenshots/Screen%20Shot%202022-01-18%20at%204.51.56%20PM.png "Preview")

This is after cranking up the node and relationship size. You can do so as shown below:

![Click here](/screenshots/Screen%20Shot%202022-01-18%20at%204.52.05%20PM.png "Node and Edge labels")

![then here](/screenshots/Screen%20Shot%202022-01-18%20at%204.52.45%20PM.png "Edit size, color")

MAC addresses and some IP addresses will still be...

# Creating a community



```
CALL gds.graph.create('myGraph', 'IP', 'CONNECTED',
    { relationshipProperties: 'count' }
);
```


```
CALL gds.labelPropagation.write('myGraph', { writeProperty: 'community' })
YIELD communityCount, ranIterations, didConverge
```


# Helpful queries

Clear out all objects in database (start over)

```
MATCH (n) DETACH DELETE n
```
