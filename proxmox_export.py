import json
import requests
import re
import os
from dotenv import load_dotenv

load_dotenv()

PROXMOX_HOST = os.getenv("PROXMOX_HOST")
USERNAME = os.getenv("USERNAME")
PASSWORD = os.getenv("PASSWORD")
NODE = os.getenv("NODE")
VERIFY_SSL = False if os.getenv("VERIFY_SSL", "false").lower() == "false" else True

resp = requests.post(
    f"{PROXMOX_HOST}/api2/json/access/ticket",
    data={"username": USERNAME, "password": PASSWORD},
    verify=VERIFY_SSL
)
data = resp.json()["data"]
ticket = data["ticket"]
csrf = data["CSRFPreventionToken"]
cookies = {"PVEAuthCookie": ticket}
headers = {"CSRFPreventionToken": csrf}

vm_list = requests.get(f"{PROXMOX_HOST}/api2/json/nodes/{NODE}/qemu", headers=headers, cookies=cookies, verify=VERIFY_SSL).json()["data"]
lxc_list = requests.get(f"{PROXMOX_HOST}/api2/json/nodes/{NODE}/lxc", headers=headers, cookies=cookies, verify=VERIFY_SSL).json()["data"]

all_vms = []

def parse_disk_size(disk_str):
    match = re.search(r"size=(\d+)([GM])", disk_str)
    if not match:
        return 0
    size, unit = match.groups()
    return int(size) if unit == "G" else int(size) * 1024

def extract_lxc_net(config):
    interfaces = []
    for key, val in config.items():
        if key.startswith("net"):
            entry = {}
            m_name = re.search(r"name=(\w+)", val)
            m_mac = re.search(r"hwaddr=([0-9A-Fa-f:]+)", val)
            m_ip = re.findall(r"ip=([\d\.]+)", val)
            if m_name:
                entry["name"] = m_name.group(1)
            if m_mac:
                entry["mac"] = m_mac.group(1).lower()
            entry["ip_addresses"] = []
            for ip in m_ip:
                if ip.startswith("127.") or ip.startswith("172."):
                    continue
                entry["ip_addresses"].append({"ip": ip, "prefix": 24})  # Префикс нет в поле, предполагаем /24
            interfaces.append(entry)
    return interfaces

for vm in vm_list + lxc_list:
    vmid = vm["vmid"]
    vtype = "qemu" if vm in vm_list else "lxc"
    config_url = f"{PROXMOX_HOST}/api2/json/nodes/{NODE}/{vtype}/{vmid}/config"
    config = requests.get(config_url, headers=headers, cookies=cookies, verify=VERIFY_SSL).json()["data"]

    ostype = config.get("ostype", None)
    ostype = None if ostype.startswith("win") or ostype == "l26" else ostype
    disk_gb = 0
    if vtype == "qemu":
        for key, val in config.items():
            if re.match(r"^(scsi|virtio|sata)\d+$", key):
                disk_gb = parse_disk_size(val)
                break
    elif vtype == "lxc" and "rootfs" in config:
        disk_gb = parse_disk_size(config["rootfs"])

    interfaces = []
    try:
        agent_data = requests.get(f"{PROXMOX_HOST}/api2/json/nodes/{NODE}/{vtype}/{vmid}/agent/network-get-interfaces",
                                  headers=headers, cookies=cookies, verify=VERIFY_SSL).json()
        for iface in agent_data.get("data", {}).get("result", []):
            ifname = iface.get("name")
            if ifname.startswith("br-") or ifname.startswith("lo") or ifname.startswith("Loopback") or ifname.startswith("veth") or ifname.startswith("docker_") or ifname.startswith("tun"):
                continue
            mac = iface.get("hardware-address", "").lower()
            ip_list = []
            for ip in iface.get("ip-addresses", []):
                if ip["ip-address-type"] != "ipv4":
                    continue
                ip_addr = ip["ip-address"]
                prefix = ip.get("prefix", 24)
                if ip_addr.startswith("127."):
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
    except:
        if vtype == "lxc":
            interfaces = extract_lxc_net(config)

    all_vms.append({
        "name": vm["name"],
        "type": vtype,
        "ostype": ostype,
        "vcpu": config.get("cores", 1),
        "ram_mb": config.get("memory", 0),
        "disk_gb": disk_gb,
        "interfaces": interfaces,
        "host": NODE
    })

with open("proxmox_vms.json", "w") as f:
    json.dump(all_vms, f, indent=2)

print("✅ VM data exported to proxmox_vms.json")
