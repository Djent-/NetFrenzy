import tqdm

import pyshark

from . import multicast

class Wireshark:
    def __init__(self, pcap_filename, keep_packets=False):
        self.filename = pcap_filename
        self.cap = pyshark.FileCapture(self.filename, keep_packets=keep_packets)
        self.ignore = []
        self.count = None
        self.do_count = True
        self.debug_at = -2

    def upload_to_neo4j(self, neo4j):
        if self.do_count and self.count is None:
            print('Counting packets in pcap. Takes approx 1ms/packet')
            self.count = 0
            for c in self.cap:
                self.count += 1
        cap_iter = None
        if self.do_count or self.count:
            cap_iter = tqdm.tqdm(self.cap, total=self.count)
        else:
            cap_iter = tqdm.tqdm(self.cap)

        debug_count = 0
        for packet in cap_iter:
            if debug_count == self.debug_at + 1:
                neo4j.debug = True
            elif debug_count > 0 and debug_count != self.debug_at:
                neo4j.debug = False

            proto = get_protocol(packet)
            time = get_time(packet)
            length = get_length(packet)
            mac_src, mac_dst, mac_tra, mac_rec = get_macs(packet)
            ip_src, ip_dst = get_ips(packet)
            port_src, port_dst = get_ports(packet)
            oui_src, oui_dst = get_oui(packet)
            service, service_layer = get_service(packet)
            ssid = get_ssid(packet)

            # Create/merge nodes for the IP addresses
            self.create_ip(neo4j, ip_src)
            self.create_ip(neo4j, ip_dst)

            # Create/merge nodes for the MAC addresses
            self.create_mac(neo4j, mac_src, oui=oui_src)
            self.create_mac(neo4j, mac_dst, oui=oui_dst)
            self.create_mac(neo4j, mac_tra)
            self.create_mac(neo4j, mac_rec)

            # Assign the IP addresses to the MAC addresses
            self.create_mac_assignment(neo4j, ip_src, mac_src)
            self.create_mac_assignment(neo4j, ip_dst, mac_dst)

            # Create or update the connection relationship for the packet
            if None not in (ip_src, ip_dst):
                # Create a connection between IP addresses
                self.create_connection(neo4j, ip_src, ip_dst, port_dst, proto, time, length, service, service_layer)
            elif None not in (mac_src, mac_dst):
                # Create a connection between MAC addresses
                self.create_connection_mac(neo4j, mac_src, mac_dst, proto, time, length, service, service_layer)
            if None not in (mac_src, mac_dst, mac_tra, mac_rec):
                # Create a connection between MAC addresses
                # This is for wlan frames that have ra and ta
                # We are connecting the sender to transmitter, receiver to destination
                self.create_connection_mac(neo4j, mac_src, mac_tra, proto, time, length, service, service_layer)
                self.create_connection_mac(neo4j, mac_rec, mac_dst, proto, time, length, service, service_layer)

            self.create_ssid(neo4j, ssid, mac_src)

            debug_count += 1

    '''
    Deprecated, creates too many edges which probably aren't useful anyway
    '''
    def create_port_relationship(self, neo4j, ip_src, ip_dst, port_src, port_dst, proto, time, length):
        props = '{'
        props += f'srcport: {port_src}, '
        props += f'dstport: {port_dst}, '
        props += f'protocol: "{proto}", '
        props += f'time: {time}, '
        props += f'length: {length}'
        props += '}'
        neo4j.new_relationship(ip_src, ip_dst, 'CONNECTED', relprops=props)
    
    def create_ip(self, neo4j, ip):
        if ip is None:
            return
        mc = multicast.ip_multicast(ip)
        mcast = ''
        if mc:
            mcast = ', multicast: true'
        neo4j.new_node('IP', f'{{name: "{ip}"{mcast}}}')
    
    def create_mac(self, neo4j, mac, oui=None):
        if mac is None:
            return
        man = ''
        if oui not in (None, 'None'):
            man = f', manufacturer: "{oui}"'
        multi = ''
        if multicast.mac_multicast(mac):
            multi = ', multicast: "likely"'
        neo4j.new_node('MAC', f'{{name: "{mac}"{man}{multi}}}')
    
    def create_mac_assignment(self, neo4j, ip, mac):
        if mac not in self.ignore and ip is not None:
            neo4j.new_relationship(ip, mac, 'ASSIGNED')
    
    def create_connection(self, neo4j, ip_src, ip_dst, port_dst, proto, time, length, service, service_layer):
        if port_dst is None:
            port_dst = -1
    
        # Create CONNECTED relationship between IPs
        query = f'''MATCH (n:IP {{name: "{ip_src}"}})
    MATCH (m:IP {{name: "{ip_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{port_dst}/{proto}", port: {port_dst}, protocol: "{proto}"}}]->(m)
        ON CREATE
            SET r += {{first_seen: {time}, last_seen: {time}, data_size: {length}, service: "{service}", service_layer: {service_layer}, count: 1}}
        ON MATCH
            SET r.first_seen = (CASE WHEN {time} > r.first_seen THEN r.first_seen ELSE {time} END)
            SET r.last_seen = (CASE WHEN {time} < r.last_seen THEN r.last_seen ELSE {time} END)
            SET r += {{data_size: r.data_size+{length}, count: r.count+1}}
    return r'''
        neo4j.raw_query(query)
        # Update service for CONNECTED relationship
        query = f'''MATCH (n:IP {{name: "{ip_src}"}})
    MATCH (m:IP {{name: "{ip_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{port_dst}/{proto}", port: {port_dst}, protocol: "{proto}"}}]->(m)
        SET r.service = (CASE WHEN {service_layer} > r.service_layer THEN "{service}" ELSE r.service END)
        SET r.service_layer = (CASE WHEN {service_layer} > r.service_layer THEN "{service_layer}" ELSE r.service_layer END)
    return r.service'''
        neo4j.raw_query(query)
    
    def create_connection_mac(self, neo4j, mac_src, mac_dst, proto, time, length, service, service_layer):
        # Create CONNECTED relationship between MACs
        query = f'''MATCH (n:MAC {{name: "{mac_src}"}})
    MATCH (m:MAC {{name: "{mac_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{proto}", protocol: "{proto}"}}]->(m)
        ON CREATE
            SET r += {{first_seen: {time}, last_seen: {time}, data_size: {length}, service: "{service}", service_layer: {service_layer}, count: 1}}
        ON MATCH
            SET r.first_seen = (CASE WHEN {time} > r.first_seen THEN r.first_seen ELSE {time} END)
            SET r.last_seen = (CASE WHEN {time} < r.last_seen THEN r.last_seen ELSE {time} END)
            SET r += {{data_size: r.data_size+{length}, count: r.count+1}}
    return r'''
        neo4j.raw_query(query)
        # Update service for CONNECTED relationship
        query = f'''MATCH (n:MAC {{name: "{mac_src}"}})
    MATCH (m:MAC {{name: "{mac_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{proto}", protocol: "{proto}"}}]->(m)
        SET r.service = (CASE WHEN {service_layer} > r.service_layer THEN "{service}" ELSE r.service END)
        SET r.service_layer = (CASE WHEN {service_layer} > r.service_layer THEN "{service_layer}" ELSE r.service_layer END)
    return r.service'''
        neo4j.raw_query(query)

    def create_ssid(self, neo4j, ssid, mac_src):
        if ssid is not None:
            neo4j.new_node('SSID', f'{{name: "{ssid}"}}')
            neo4j.new_relationship(mac_src, ssid, 'ADVERTISES')

def get_protocol(packet):
    for layer in packet.layers:
        if layer.layer_name == 'udp':
            return 'udp'
        elif layer.layer_name == 'tcp':
            return 'tcp'
    if 'ip' in packet:
        # eth -> ip -> ???
        return packet.layers[2].layer_name
    else:
        # eth -> ???
        return packet.layers[1].layer_name

def get_macs(packet):
    if 'eth' in packet:
        mac_src = packet.eth.get_field('src')
        mac_dst = packet.eth.get_field('dst')
        mac_tra = None
        mac_rec = None
    if 'wlan' in packet:
        mac_src = packet.wlan.get_field('sa')
        mac_dst = packet.wlan.get_field('da')
        mac_tra = packet.wlan.get_field('ta')
        mac_rec = packet.wlan.get_field('ra')
        if mac_src == mac_tra:
            mac_tra = None
        if mac_dst == mac_rec:
            mac_rec = None

    return mac_src, mac_dst, mac_tra, mac_rec

def get_ips(packet):
    for layer in packet.layers:
        if layer.layer_name == 'ip':
            return layer.src, layer.dst
    return None, None

def get_ports(packet):
    for layer in packet.layers:
        if layer.layer_name in ('udp', 'tcp'):
            return layer.srcport, layer.dstport
    return None, None

def get_time(packet):
    return float(packet.sniff_timestamp)

def get_length(packet):
    return int(packet.captured_length)

def get_oui(packet):
    oui_src = None
    if 'eth' in packet and 'src_oui_resolved' in packet.eth.field_names:
        oui_src = packet.eth.src_oui_resolved
    oui_dst = None
    if 'eth' in packet and 'dst_oui_resolved' in packet.eth.field_names:
        oui_dst = packet.eth.dst_oui_resolved
    return oui_src, oui_dst

def get_service(packet):
    # What could potentially have many other formats in lower layers
    for service in ('http', 'https', 'ftp'):
        if service in packet:
            return service, 999

    '''
    Some services reported by Wireshark/pyshark need to be ignored,
    like this first example 'data-text-lines' which is a layer lower
    than HTTP (the HTML itself) but we don't care about that really
    '''
    ignore = ('data-text-lines', 'data', 'mime_multipart')
    for l in range(-1, 0 - len(packet.layers), -1):
        if packet.layers[l].layer_name not in ignore:
            return packet.layers[l].layer_name, l+len(packet.layers)
    return "unknown", 0-len(packet.layers)

def get_ssid(packet):
    # This is the Wildcard SSID.
    # It's noisy and I'm not sure what to make of it
    ignore = ('SSID: ')
    ssid = None
    if 'wlan.mgt' in packet:
        ssid = packet['wlan.mgt'].get_field('wlan_ssid')
    if ssid is not None and ssid in ignore:
        ssid = None
    return ssid