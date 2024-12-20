from collections import deque
import time
import tqdm

import pyshark
from ouilookup import OuiLookup

from . import multicast

class Pcap:
    def __init__(self, pcap_filename, interface, keep_packets=False):
        self.filename = pcap_filename
        self.interface = interface
        if self.filename:
            self.cap = pyshark.FileCapture(self.filename, keep_packets=keep_packets)
        elif self.interface:
            self.cap = pyshark.LiveCapture(interface=self.interface)
        self.ignore = []
        self.count = None
        self.do_count = True
        self.debug_at = -2
        self.debug_time = False
        self._time_start = 0
        self.debug_time_neo4j = 0
        self.total_time_start = time.time()
        self.total_time_netfrenzy = 0
        self.debug_cache = False
        self.cache = {}
        self.cache_max = 0
        self.cache_init()
        self.reduce = False

    def start_process(self, neo4j):
        if self.filename:
            self.upload_to_neo4j(neo4j)
        elif self.interface:
            self.begin_capture(neo4j)

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
            self.process(neo4j, packet)
            debug_count += 1

        self.print_debug_time()
        self.print_cache_stats()

    def begin_capture(self, neo4j):
        if not self.reduce:
            self.reduce = True
            print('Enabling --reduce to ensure NetFrenzy keeps up with live capture')

        for packet in self.cap.sniff_continuously():
            self.process(neo4j, packet)

    def process(self, neo4j, packet):
        proto = get_protocol(packet)
        macs = get_macs(packet, cached=self.is_cached)
        ip_src, ip_dst = get_ips(packet)
        port_src, port_dst = get_ports(packet)
        ssid, frame_type = get_ssid(packet)
        time, length, service, service_layer = None, None, None, None
        if not self.reduce:
            time = get_time(packet)
            length = get_length(packet)
            service, service_layer = get_service(packet)

        # Create/merge nodes for the IP addresses
        self.create_ip(neo4j, ip_src)
        self.create_ip(neo4j, ip_dst)

        # Create/merge nodes for the MAC addresses
        self.create_macs(neo4j, macs)

        # Assign the IP addresses to the MAC addresses
        self.create_mac_assignment(neo4j, ip_src, macs['src']['mac'])
        self.create_mac_assignment(neo4j, ip_dst, macs['dst']['mac'])

        # Create or update the connection relationship for the packet
        if None not in (ip_src, ip_dst):
            # Create a connection between IP addresses
            self.create_connection_ip(neo4j, ip_src, ip_dst, port_dst, proto, time, length, service, service_layer)
        elif None not in (macs['src']['mac'], macs['dst']['mac']):
            # Create a connection between MAC addresses
            self.create_connection_mac(neo4j, macs['src']['mac'], macs['dst']['mac'], proto, time, length, service, service_layer, frame_type)
        if None not in (macs['src']['mac'], macs['dst']['mac'], macs['tra']['mac'], macs['rec']['mac']):
            # Create a connection between MAC addresses
            # This is for wlan frames that have ra and ta
            # We are connecting the sender to transmitter, receiver to destination
            self.create_connection_mac(neo4j, macs['src']['mac'], macs['tra']['mac'], proto, time, length, service, service_layer, frame_type)
            self.create_connection_mac(neo4j, macs['rec']['mac'], macs['dst']['mac'], proto, time, length, service, service_layer, frame_type)

        self.create_ssid(neo4j, ssid, frame_type, macs['src']['mac'])

    def debug_time_start(self):
        if self.debug_time:
            self._time_start = time.time()

    def debug_time_end(self):
        if self.debug_time:
            _time_end = time.time()
            self.debug_time_neo4j += _time_end - self._time_start

    def print_debug_time(self):
        self.debug_time_neo4j = time.time()
        if self.debug_time:
            print(f'Time in Neo4j: {self.debug_time_neo4j}')
            print(f'Total time: {self.total_time_start - self.total_time_netfrenzy}')
            print(f'Difference: {self.total_time_start - self.total_time_netfrenzy - self.debug_time_neo4j}')

    def cache_init(self):
        self.cache = {
            'IP': {
                'cache': deque([]),
                'hits': 0,
                'misses': 0,
            },
            'MAC': {
                'cache': deque([]),
                'hits': 0,
                'misses': 0,
            },
            'ASSIGN': {
                'cache': deque([]),
                'hits': 0,
                'misses': 0,
            },
            'SSID': {
                'cache': deque([]),
                'hits': 0,
                'misses': 0,
            },
            'ADVERTISES': {
                'cache': deque([]),
                'hits': 0,
                'misses': 0,
            },
            'PROBES': {
                'cache': deque([]),
                'hits': 0,
                'misses': 0,
            },
            'PROBE_RESPONSE': {
                'cache': deque([]),
                'hits': 0,
                'misses': 0,
            },
        }
        self.cache_max = 50

    def print_cache_stats(self):
        if not self.debug_cache:
            return
        keys = ['IP', 'MAC', 'ASSIGN', 'SSID', 'ADVERTISES', 'PROBES']
        print(f'cache_max: {self.cache_max}')
        for k in keys:
            print(f'cache[{k}]:')
            print(f'\tHits:\t{self.cache[k]["hits"]}')
            print(f'\tMiss:\t{self.cache[k]["misses"]}')
            print(f'\tUse:\t{len(self.cache[k]["cache"])}/{self.cache_max}')

    def cached(self, value, _type):
        is_cached = False
        if value in self.cache[_type]['cache']:
            is_cached = True
            self.cache[_type]['hits'] += 1
        else:
            self.cache[_type]['misses'] += 1
            self.cache[_type]['cache'].append(value)
            if len(self.cache[_type]['cache']) > self.cache_max:
                self.cache[_type]['cache'].popleft()
        return is_cached

    # Like cached(), but doesn't update cache
    def is_cached(self, value, _type):
        cached = False
        if value in self.cache[_type]['cache']:
            cached = True
            self.cache[_type]['hits'] += 1
        else:
            self.cache[_type]['misses'] += 1
        return cached

    def create_ip(self, neo4j, ip):
        if ip is None:
            return
        if self.cached(ip, 'IP'):
            return
        properties = {}
        properties['multicast'] = multicast.ip_multicast(ip)
        self.debug_time_start()
        neo4j.create_node('IP', ip, properties=properties)
        self.debug_time_end()
    
    def create_macs(self, neo4j, macs):
        if macs is None:
            return
        for k in macs:
            mac = macs[k]['mac']
            if mac is None:
                continue
            if self.cached(mac, 'MAC'):
                continue
            oui = macs[k]['oui']
            properties = {}
            properties['manufacturer'] = oui
            properties['multicast'] = multicast.mac_multicast(mac)
            self.debug_time_start()
            neo4j.create_node('MAC', mac, properties=properties)
            self.debug_time_end()
    
    def create_mac_assignment(self, neo4j, ip, mac):
        if mac not in self.ignore and ip is not None:
            if self.cached([ip, mac], 'ASSIGN'):
                return
            self.debug_time_start()
            neo4j.new_relationship(ip, mac, 'ASSIGNED')
            self.debug_time_end()
    
    def create_connection_ip(self, neo4j, ip_src, ip_dst, port_dst, proto, time, length, service, service_layer):
        if self.reduce:
            self.create_connection_ip_reduced(neo4j, ip_src, ip_dst, port_dst, proto)
        else:
            self.create_connection_ip_full(neo4j, ip_src, ip_dst, port_dst, proto, time, length, service, service_layer)

    def create_connection_ip_full(self, neo4j, ip_src, ip_dst, port_dst, proto, time, length, service, service_layer):
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
        self.debug_time_start()
        neo4j.raw_query(query)
        self.debug_time_end()
        # Update service for CONNECTED relationship
        query = f'''MATCH (n:IP {{name: "{ip_src}"}})
    MATCH (m:IP {{name: "{ip_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{port_dst}/{proto}", port: {port_dst}, protocol: "{proto}"}}]->(m)
        SET r.service = (CASE WHEN {service_layer} > r.service_layer THEN "{service}" ELSE r.service END)
        SET r.service_layer = (CASE WHEN {service_layer} > r.service_layer THEN "{service_layer}" ELSE r.service_layer END)
    return r.service'''
        self.debug_time_start()
        neo4j.raw_query(query)
        self.debug_time_end()
    
    def create_connection_ip_reduced(self, neo4j, ip_src, ip_dst, port_dst, proto):
        if port_dst is None:
            port_dst = -1
    
        # Create CONNECTED relationship between IPs
        query = f'''MATCH (n:IP {{name: "{ip_src}"}})
    MATCH (m:IP {{name: "{ip_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{port_dst}/{proto}", port: {port_dst}, protocol: "{proto}"}}]->(m)
    return r'''
        self.debug_time_start()
        neo4j.raw_query(query)
        self.debug_time_end()
    
    def create_connection_mac(self, neo4j, mac_src, mac_dst, proto, time, length, service, service_layer, frame_type):
        if frame_type == 'probe_response':
            return self.create_probe_response_mac(neo4j, mac_src, mac_dst)
        if self.reduce:
            self.create_connection_mac_reduced(neo4j, mac_src, mac_dst, proto)
        else:
            self.create_connection_mac_full(neo4j, mac_src, mac_dst, proto, time, length, service, service_layer)
    
    def create_connection_mac_full(self, neo4j, mac_src, mac_dst, proto, time, length, service, service_layer):
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
        self.debug_time_start()
        neo4j.raw_query(query)
        self.debug_time_end()
        # Update service for CONNECTED relationship
        query = f'''MATCH (n:MAC {{name: "{mac_src}"}})
    MATCH (m:MAC {{name: "{mac_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{proto}", protocol: "{proto}"}}]->(m)
        SET r.service = (CASE WHEN {service_layer} > r.service_layer THEN "{service}" ELSE r.service END)
        SET r.service_layer = (CASE WHEN {service_layer} > r.service_layer THEN "{service_layer}" ELSE r.service_layer END)
    return r.service'''
        self.debug_time_start()
        neo4j.raw_query(query)
        self.debug_time_end()

    def create_connection_mac_reduced(self, neo4j, mac_src, mac_dst, proto):
        # Create CONNECTED relationship between MACs
        query = f'''MATCH (n:MAC {{name: "{mac_src}"}})
    MATCH (m:MAC {{name: "{mac_dst}"}})
    MERGE (n)-[r:CONNECTED {{name: "{proto}", protocol: "{proto}"}}]->(m)
    return r'''
        self.debug_time_start()
        neo4j.raw_query(query)
        self.debug_time_end()

    def create_probe_response_mac(self, neo4j, mac_src, mac_dst):
        # Create CONNECTED relationship between MACs
        query = f'''MATCH (n:MAC {{name: "{mac_src}"}})
    MATCH (m:MAC {{name: "{mac_dst}"}})
    MERGE (n)-[r:PROBE_RESPONSE]->(m)
    return r'''
        self.debug_time_start()
        neo4j.raw_query(query)
        self.debug_time_end()

    def create_ssid(self, neo4j, ssid, frame_type, mac_src):
        if ssid is None:
            return
        if not self.cached(ssid, 'SSID'):
            self.debug_time_start()
            neo4j.create_node('SSID', ssid)
            self.debug_time_end()
        if mac_src is None:
            return
        relationship = 'ADVERTISES'
        if frame_type == 'probe':
            relationship = 'PROBES'
        elif frame_type == 'probe_response':
            return
        if not self.cached([mac_src, ssid], relationship):
            self.debug_time_start()
            neo4j.new_relationship(mac_src, ssid, relationship)
            self.debug_time_end()

def get_protocol(packet):
    for layer in packet.layers:
        if layer.layer_name == 'udp':
            return 'udp'
        elif layer.layer_name == 'tcp':
            return 'tcp'
    if 'ip' in packet or 'ipv6' in packet:
        # eth -> ip -> ???
        return packet.layers[2].layer_name
    else:
        # eth -> ???
        return packet.layers[1].layer_name

def get_macs(packet, cached=None):
    macs = {}
    macs['src'] = {'mac': None, 'oui': None}
    macs['dst'] = {'mac': None, 'oui': None}
    macs['tra'] = {'mac': None, 'oui': None}
    macs['rec'] = {'mac': None, 'oui': None}

    if 'eth' in packet:
        macs['src']['mac'] = packet.eth.get_field('src')
        macs['dst']['mac'] = packet.eth.get_field('dst')
    if 'wlan' in packet:
        macs['src']['mac'] = packet.wlan.get_field('sa')
        macs['dst']['mac'] = packet.wlan.get_field('da')
        macs['tra']['mac'] = packet.wlan.get_field('ta')
        macs['rec']['mac'] = packet.wlan.get_field('ra')
        if macs['src']['mac'] == macs['tra']['mac']:
            macs['tra']['mac'] = None
        if macs['dst']['mac'] == macs['rec']['mac']:
            macs['rec']['mac'] = None
    
    for k in macs:
        if macs[k]['mac'] is not None:
            if cached is not None and cached(macs[k]['mac'], 'MAC'):
                continue
            macs[k]['oui'] = get_oui(macs[k]['mac'])

    return macs

'''
Deprecated. Use get_macs()
'''
def get_macs_old(packet):
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
        if layer.layer_name in ('ip', 'ipv6'):
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

def get_oui(mac):
    try:
        for k, v in OuiLookup().query(mac)[0].items():
            return v
    except:
        pass
    return None

'''
Deprecated. Use get_oui()
'''
def get_oui_old(packet):
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
    ignore = ('SSID')
    ssid = None
    frame_type = 'beacon'
    if 'wlan.mgt' in packet:
        # Format: Tag: SSID parameter set: "TMobileWiFi-2.4GHz"
        length = packet['wlan.mgt'].get_field('wlan_tag_length')
        tag = packet['wlan.mgt'].get_field('wlan_tag')
        if length and int(length) > 0 and tag:
            ssid = tag[len(tag)-int(length)-1:-1]
    if 'wlan' in packet:
        if packet.wlan.fc_type_subtype == '0x0004':
            frame_type = 'probe'
        if packet.wlan.fc_type_subtype == '0x0005':
            frame_type = 'probe_response'
    if ssid is not None and ssid in ignore:
        ssid = None
    return ssid, frame_type
