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
    match = re.search(r"size=(\d+)([GMK])", str(disk_str))
    if not match:
        return 0
    size, unit = match.groups()
    if unit == "G":
      return int(size)
    elif unit == "M":
      return round(int(size) / 1024)
    else:
      return round(int(size) / 1024 / 1024)

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
        disk_size_gb = parse_disk_size(config["rootfs"])
        disk_info = {
            "name": "rootfs",
            "size_gb": disk_size_gb,
            "description": str(config["rootfs"])
        }
        disks.append(disk_info)

    # Check for additional mount points (mp0, mp1, etc.)
    for key, val in config.items():
        if re.match(r"^mp\d+$", key):
            disk_size_gb = parse_disk_size(str(val))
            disk_info = {
                "name": key,
                "size_gb": disk_size_gb,
                "description": str(val)
            }
            disks.append(disk_info)

    return disks

def extract_lxc_net(config):
    interfaces = []
    for key, val in config.items():
        if key.startswith("net"):
            entry = {}
            m_name = re.search(r"name=(\w+)", val)
            m_mac = re.search(r"hwaddr=([0-9A-Fa-f:]+)", val)
            m_ip4 = re.findall(r"ip=([\d\.]+/\d+)", val)
            m_ip6 = re.findall(r"ip6=([0-9a-fA-F:]+/\d+)", val)

            if m_name:
                entry["name"] = m_name.group(1)
            if m_mac:
                entry["mac"] = m_mac.group(1).lower()

            entry["ip_addresses"] = []

            # IPv4 addresses
            for ip_cidr in m_ip4:
                ip, prefix = ip_cidr.split('/')
                if ip.startswith("127.") or ip.startswith("172."):
                    continue
                entry["ip_addresses"].append({"ip": ip, "prefix": int(prefix)})

            # IPv6 addresses
            for ip_cidr in m_ip6:
                ip, prefix = ip_cidr.split('/')
                if ip.lower().startswith("fe80::"):
                    continue  # Skip link-local addresses
                entry["ip_addresses"].append({"ip": ip, "prefix": int(prefix)})

            interfaces.append(entry)
    return interfaces

def get_qemu_net_interfaces(config):
    interfaces = []
    for key, val in config.items():
        if re.match(r"^net\d+$", key):
            entry = {}
            m_mac = re.search(r"([0-9A-Fa-f:]{17})", val)
            m_bridge = re.search(r"bridge=(\w+)", val)

            if m_mac:
                entry["mac"] = m_mac.group(1).lower()
            if m_bridge:
                entry["name"] = m_bridge.group(1)
            else:
                entry["name"] = f"net{len(interfaces)}"

            entry["ip_addresses"] = []
            interfaces.append(entry)
    return interfaces

def is_ipv6(ip):
    return ':' in ip

def should_skip_interface(ifname):
    """Skip virtual/container interfaces that aren't relevant for NetBox"""
    skip_prefixes = ["br-", "lo", "Loopback", "veth", "docker", "tun", "tailscale"]
    return any(ifname.startswith(prefix) for prefix in skip_prefixes)

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
                config = proxmox.nodes(node_name).qemu(vmid).config.get() if vtype == "qemu" else proxmox.nodes(node_name).lxc(vmid).config.get()
            except Exception as e:
                print(f"ERROR: Failed to get config for {vmid}: {e}")
                continue

            ostype = config.get("ostype", None)
            ostype = None if ostype and (ostype.startswith("win") or ostype == "l26") else ostype

            # Extract disk information
            disks = []
            if vtype == "qemu":
                disks = extract_disk_info(config)
            else:  # LXC
                disks = extract_lxc_disk_info(config)

            print(f"INFO: Found {len(disks)} disks for VM {vmid}")

            interfaces = []

            # Try agent data for QEMU VMs only
            if vtype == "qemu":
                try:
                    agent_data = proxmox.nodes(node_name).qemu(vmid).agent.get("network-get-interfaces")

                    for iface in agent_data.get("result", []):
                        ifname = iface.get("name")
                        if should_skip_interface(ifname):
                            continue

                        mac = iface.get("hardware-address", "").lower()
                        ip_list = []

                        for ip in iface.get("ip-addresses", []):
                            ip_addr = ip["ip-address"]
                            prefix = ip.get("prefix", 24 if ip["ip-address-type"] == "ipv4" else 64)

                            # Skip loopback addresses
                            if ip_addr.startswith("127."):
                                continue

                            # Skip private docker networks
                            if ip_addr.startswith("172."):
                                continue

                            # Skip IPv6 link-local addresses
                            if is_ipv6(ip_addr) and ip_addr.lower().startswith("fe80::"):
                                continue

                            ip_list.append({
                                "ip": ip_addr,
                                "prefix": prefix
                            })

                        interfaces.append({
                            "name": ifname,
                            "mac": mac,
                            "ip_addresses": ip_list
                        })
                except Exception as e:
                    print(f"WARN: Agent data not available for {vmid}: {e}")
                    # Fallback to config-based interface detection for QEMU
                    interfaces = get_qemu_net_interfaces(config)
            else:
                # For LXC, always use config-based interface detection
                interfaces = extract_lxc_net(config)

            vm_data = {
                "name": vm["name"],
                "type": vtype,
                "status": "running",
                "ostype": ostype,
                "vcpu": config.get("cores", 1),
                "ram_mb": int(config.get("memory", 0)),  # Ensure integer
                "disks": disks,  # Individual disk information
                "interfaces": interfaces,
                "host": node_name
            }

            all_vms.append(vm_data)

            # Count IPv4 and IPv6 addresses for summary
            ipv4_count = sum(len([ip for ip in iface["ip_addresses"] if not is_ipv6(ip["ip"])]) for iface in interfaces)
            ipv6_count = sum(len([ip for ip in iface["ip_addresses"] if is_ipv6(ip["ip"])]) for iface in interfaces)

            total_disk_gb = sum(disk["size_gb"] for disk in disks)
            print(f"INFO: Added {vm['name']} with {len(interfaces)} interfaces ({ipv4_count} IPv4, {ipv6_count} IPv6), {len(disks)} disks ({total_disk_gb}GB total)")

    except Exception as e:
        print(f"ERROR: Failed to process node {node_name}: {e}")

print(f"\nINFO: Total VMs processed: {len(all_vms)}")

with open("proxmox_vms.json", "w") as f:
    json.dump(all_vms, f, indent=2)

print("INFO: VM data exported to proxmox_vms.json")
