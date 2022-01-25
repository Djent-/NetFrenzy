var helpfulqueries = {
	"Find all 80/tcp connections": "MATCH (n)-[r:CONNECTED {port: 80, protocol: \"tcp\"}]->(m) RETURN *",
	"Find all connections from an IP": "MATCH (n:IP {name: \"192.168.119.151\"})-[r:CONNECTED]->(m) RETURN *",
	"Display all nodes and relationships": "MATCH (n), (o)-[r]-(m) RETURN *",
	"Display all paths which do not involve a multicast address": "MATCH path=(n)-[r]-(m) WHERE NONE(n IN nodes(path) WHERE exists(n.multicast)) RETURN path",
	"Top 10 IPs with most outbound connections": "MATCH (n:IP), (m:IP), (n)-[r:CONNECTED]->(m) WITH n, count(r) AS rel_count ORDER BY rel_count DESC LIMIT 10 MATCH p=(m)<-[r:CONNECTED]-(n) RETURN p",
}

// vim: ts=2 sts=2
