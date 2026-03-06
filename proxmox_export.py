#!/usr/bin/env python3

import json
import re
import os
from dotenv import load_dotenv
from proxmoxer import ProxmoxAPI

load_dotenv()

PROXMOX_HOST = os.getenv("PROXMOX_HOST")
PROXMOX_USER = os.getenv("PROXMOX_USER")  # format: username@realm
API_TOKEN_NAME = os.getenv("API_TOKEN_NAME")
API_TOKEN_VALUE = os.getenv("API_TOKEN_VALUE")
VERIFY_SSL = False if os.getenv("VERIFY_SSL", "false").lower() == "false" else True

print(f"INFO: Connecting to: {PROXMOX_HOST}")
print(f"INFO: User: {PROXMOX_USER}")

try:
    # Initialize Proxmox API with token authentication
    proxmox = ProxmoxAPI(
        PROXMOX_HOST,
        user=PROXMOX_USER,
        token_name=API_TOKEN_NAME,
        token_value=API_TOKEN_VALUE,
        verify_ssl=VERIFY_SSL
    )

    # Test connection
    version = proxmox.version.get()
    print(f"INFO: Connected to Proxmox VE {version['version']}")

    # Get all nodes
    nodes = proxmox.nodes.get()
    print(f"INFO: Found {len(nodes)} nodes: {[node['node'] for node in nodes]}")

except Exception as e:
    print(f"ERROR: Connection failed: {e}")
    exit(1)

all_vms = []

def parse_disk_size(disk_str):
    if not disk_str:
        return 0
    match = re.search(r"size=(\d+)([GM])", str(disk_str))
    if not match:
        return 0
    size, unit = match.groups()
    return int(size) if unit == "G" else int(size) * 1024

def extract_disk_info(config):
    """Extract individual disk information from QEMU VM config"""
    disks = []
    disk_patterns = [r"^(scsi\d+)$", r"^(virtio\d+)$", r"^(sata\d+)$", r"^(ide\d+)$"]

    for key, val in config.items():
        # Check if this key matches any disk pattern
        for pattern in disk_patterns:
            if re.match(pattern, key):
                # Skip CD-ROM/cloudinit disks
                if "media=cdrom" in str(val) or "cloudinit" in str(val):
                    continue

                # Parse disk size
                disk_size_gb = parse_disk_size(str(val))

                disk_info = {
                    "name": key,
                    "size_gb": disk_size_gb,
                    "description": str(val)
                }
                disks.append(disk_info)
                break

    return disks

def extract_lxc_disk_info(config):
    """Extract disk information from LXC container config"""
    disks = []
    if "rootfs" in config:
        disks.append({
            "name":        "rootfs",
            "size_gb":     parse_disk_size(config["rootfs"]),
            "description": str(config["rootfs"])
        })
    for key, val in config.items():
        if re.match(r"^mp\d+$", key):
            disks.append({
                "name":        key,
                "size_gb":     parse_disk_size(str(val)),
                "description": str(val)
            })
    return disks

def extract_lxc_net(config):
    interfaces = []
    for key, val in config.items():
        if key.startswith("net"):
            val = str(val)
            m_name   = re.search(r"name=(\w+)", val)
            m_mac    = re.search(r"hwaddr=([0-9A-Fa-f:]+)", val)
            m_ip4    = re.findall(r"ip=([\d\.]+/\d+)", val)
            m_ip6    = re.findall(r"ip6=([0-9a-fA-F:]+/\d+)", val)
            m_tag    = re.search(r"tag=(\d+)", val)

            ip_addresses = []
            for ip_cidr in m_ip4:
                ip, prefix = ip_cidr.split("/")
                if ip.startswith("127."):
                    continue
                ip_addresses.append({"ip": ip, "prefix": int(prefix)})
            for ip_cidr in m_ip6:
                ip, prefix = ip_cidr.split("/")
                if ip.lower().startswith("fe80:"):
                    continue
                ip_addresses.append({"ip": ip, "prefix": int(prefix)})

            interfaces.append({
                "name":        m_name.group(1) if m_name else key,
                "mac":         m_mac.group(1).lower() if m_mac else None,
                "vlan_tag":    int(m_tag.group(1)) if m_tag else None,
                "ip_addresses": ip_addresses,
            })
    return interfaces

def get_qemu_net_interfaces(config):
    interfaces = []
    for key, val in config.items():
        if re.match(r"^net\d+$", key):
            val = str(val)
            m_mac    = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", val)
            m_bridge = re.search(r"bridge=(\w+)", val)
            m_tag    = re.search(r"tag=(\d+)", val)

            interfaces.append({
                "name":         m_bridge.group(1) if m_bridge else key,
                "mac":          m_mac.group(1).lower() if m_mac else None,
                "vlan_tag":     int(m_tag.group(1)) if m_tag else None,
                "ip_addresses": [],
            })
    return interfaces

def is_ipv6(ip):
    return ':' in ip

def should_skip_interface(ifname):
    """Skip virtual/container interfaces that aren't relevant for NetBox"""
    skip_prefixes = ["br-", "lo", "Loopback", "veth", "docker", "tun", "tailscale"]
    return any(ifname.startswith(prefix) for prefix in skip_prefixes)

def enrich_interfaces_with_agent(proxmox, node_name, vmid, interfaces):
    try:
        agent_data = proxmox.nodes(node_name).qemu(vmid).agent.get("network-get-interfaces")
    except Exception as e:
        print(f"WARN: Agent data not available for {vmid}: {e}")
        return

    mac_to_iface = {iface["mac"]: iface for iface in interfaces if iface.get("mac")}

    for agent_iface in agent_data.get("result", []):
        ifname = agent_iface.get("name", "")
        if should_skip_interface(ifname):
            continue

        mac = agent_iface.get("hardware-address", "").lower()
        iface = mac_to_iface.get(mac)
        if iface is None:
            continue

        for ip in agent_iface.get("ip-addresses", []):
            addr   = ip["ip-address"]
            prefix = ip.get("prefix", 24 if ip["ip-address-type"] == "ipv4" else 64)
            if addr.startswith("127."):
                continue
            if is_ipv6(addr) and addr.lower().startswith("fe80:"):
                continue
            iface["ip_addresses"].append({"ip": addr, "prefix": prefix})

# Process all nodes
for node in nodes:
    node_name = node['node']
    print(f"\nINFO: Processing node: {node_name}")

    try:
        vm_list = proxmox.nodes(node_name).qemu.get()
        lxc_list = proxmox.nodes(node_name).lxc.get()

        print(f"INFO: Found {len(vm_list)} QEMU VMs")
        print(f"INFO: Found {len(lxc_list)} LXC containers")

        for vm in vm_list + lxc_list:
            vmid = vm["vmid"]
            vtype = "qemu" if vm in vm_list else "lxc"
            vm_status = vm.get("status", "unknown")

            print(f"INFO: Processing {vtype} VM {vmid}: {vm['name']} (status: {vm_status})")

            # Check if VM/LXC is running
            if vm_status != "running":
                print(f"WARN: VM {vmid} is not running, marking as offline")
                vm_data = {
                    "name": vm["name"],
                    "type": vtype,
                    "status": "offline",
                    "host": node_name
                }
                all_vms.append(vm_data)
                continue

            try:
                if vtype == "qemu":
                    config = proxmox.nodes(node_name).qemu(vmid).config.get()
                else:
                    config = proxmox.nodes(node_name).lxc(vmid).config.get()

            except Exception as e:
                print(f"ERROR: Failed to get config for {vmid}: {e}")
                continue

            ostype = config.get("ostype", None)
            ostype = None if ostype and (ostype.startswith("win") or ostype == "l26") else ostype

            if vtype == "qemu":
                disks      = extract_disk_info(config)
                interfaces = get_qemu_net_interfaces(config)
                enrich_interfaces_with_agent(proxmox, node_name, vmid, interfaces)
            else:  # LXC
                disks      = extract_lxc_disk_info(config)
                interfaces = extract_lxc_net(config)

            print(f"INFO: Found {len(disks)} disks for VM {vmid}")

            vm_data = {
                "name": vm["name"],
                "type": vtype,
                "status": "running",
                "ostype": ostype,
                "vcpu": config.get("cores", 1),
                "ram_mb": int(config.get("memory", 0)),
                "disks": disks,
                "interfaces": interfaces,
                "host": node_name
            }

            all_vms.append(vm_data)

            # Count IPv4 and IPv6 addresses for summary
            ipv4_count  = sum(1 for iface in interfaces for ip in iface["ip_addresses"] if not is_ipv6(ip["ip"]))
            ipv6_count  = sum(1 for iface in interfaces for ip in iface["ip_addresses"] if is_ipv6(ip["ip"]))
            total_disk  = sum(d["size_gb"] for d in disks)
            print(f"INFO: Added {vm['name']} with {len(interfaces)} interfaces "
                  f"({ipv4_count} IPv4, {ipv6_count} IPv6), {len(disks)} disks ({total_disk} GB total)")

    except Exception as e:
        print(f"ERROR: Failed to process node {node_name}: {e}")

print(f"\nINFO: Total VMs processed: {len(all_vms)}")

with open("proxmox_vms.json", "w") as f:
    json.dump(all_vms, f, indent=2)

print("INFO: VM data exported to proxmox_vms.json")
