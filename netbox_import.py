import json
import requests
import random
import os
from dotenv import load_dotenv

load_dotenv()

NETBOX_URL = os.getenv("NETBOX_URL")
NETBOX_TOKEN = os.getenv("NETBOX_TOKEN")
SITE_ID = int(os.getenv("SITE_ID", 1))
CLUSTER_ID = int(os.getenv("CLUSTER_ID", 1))
DEVICE_NAME = os.getenv("DEVICE_NAME")
VERIFY_SSL = False if os.getenv("VERIFY_SSL", "false").lower() == "false" else True

HEADERS = {
    "Authorization": f"Token {NETBOX_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

def get_or_create(url, query, payload):
    r = requests.get(f"{url}?{query}", headers=HEADERS, verify=VERIFY_SSL)
    results = r.json()["results"]
    if results:
        return results[0]
    r = requests.post(url, headers=HEADERS, json=payload, verify=VERIFY_SSL)
    return r.json()

def find_existing_vm(name):
    r = requests.get(f"{NETBOX_URL}/virtualization/virtual-machines/?name={name}", headers=HEADERS, verify=VERIFY_SSL)
    return r.json()["results"][0] if r.json()["results"] else None

def get_device_id(name):
    r = requests.get(f"{NETBOX_URL}/dcim/devices/?name={name}", headers=HEADERS, verify=VERIFY_SSL)
    return r.json()["results"][0]["id"] if r.json()["results"] else None

def create_or_get_vrf(name):
    r = requests.get(f"{NETBOX_URL}/ipam/vrfs/?name={name}", headers=HEADERS, verify=VERIFY_SSL)
    if r.json()["results"]:
        return r.json()["results"][0]["id"]
    vrf_payload = {
        "name": name,
        "rd": f"1:{random.randint(10, 100)}",
        "enforce_unique": True
    }
    r = requests.post(f"{NETBOX_URL}/ipam/vrfs/", headers=HEADERS, json=vrf_payload, verify=VERIFY_SSL)
    return r.json()["id"]

def create_or_get_platform(name):
    if not name:
        return None
    r = requests.get(f"{NETBOX_URL}/dcim/platforms/?name={name}", headers=HEADERS, verify=VERIFY_SSL)
    if r.json()["results"]:
        return r.json()["results"][0]["id"]
    r = requests.post(f"{NETBOX_URL}/dcim/platforms/", headers=HEADERS, verify=VERIFY_SSL, json={"name": name, "slug": name.lower().replace(" ", "-")})
    return r.json()["id"]

def get_or_create_mac(mac_str):
    if not mac_str:
        return None
    r = requests.get(f"{NETBOX_URL}/dcim/mac-addresses/?mac_address={mac_str}", headers=HEADERS, verify=VERIFY_SSL)
    results = r.json()["results"]
    if results:
        return results[0]["id"]
    r = requests.post(f"{NETBOX_URL}/dcim/mac-addresses/", headers=HEADERS, verify=VERIFY_SSL, json={"mac_address": mac_str})
    return r.json()["id"]

with open("proxmox_vms.json") as f:
    vms = json.load(f)

device_id = get_device_id(DEVICE_NAME)

for vm in vms:
    platform_id = create_or_get_platform(vm["ostype"]) if vm["ostype"] else None
    disk_mb = int(float(vm["disk_gb"]) * 1000)

    existing_vm = find_existing_vm(vm["name"])

    vm_payload = {
        "name": vm["name"],
        "status": "active",
        "site": SITE_ID,
        "cluster": CLUSTER_ID,
        "vcpus": int(vm["vcpu"]),
        "memory": (int(vm["ram_mb"]) / 1024) * 1000,
        "disk": disk_mb,
        "device": device_id,
    }
    if platform_id:
        vm_payload["platform"] = platform_id

    if existing_vm:
        vm_id = existing_vm["id"]
        requests.patch(f"{NETBOX_URL}/virtualization/virtual-machines/{vm_id}/", headers=HEADERS, verify=VERIFY_SSL, json=vm_payload)
    else:
        new_vm = requests.post(f"{NETBOX_URL}/virtualization/virtual-machines/", headers=HEADERS, verify=VERIFY_SSL, json=vm_payload)
        vm_id = new_vm.json()["id"]

    iface_response = requests.get(f"{NETBOX_URL}/virtualization/interfaces/?virtual_machine_id={vm_id}", headers=HEADERS, verify=VERIFY_SSL)
    existing_ifaces = {iface["name"]: iface for iface in iface_response.json()["results"]}

    for iface in vm["interfaces"]:
        iface_name = iface["name"]
        existing_iface = existing_ifaces.get(iface_name)

        iface_payload = {
            "virtual_machine": vm_id,
            "name": iface_name
        }

        if existing_iface:
            iface_id = existing_iface["id"]
            requests.patch(f"{NETBOX_URL}/virtualization/interfaces/{iface_id}/", headers=HEADERS, verify=VERIFY_SSL, json=iface_payload)
        else:
            created_iface = requests.post(f"{NETBOX_URL}/virtualization/interfaces/", headers=HEADERS, verify=VERIFY_SSL, json=iface_payload)
            iface_id = created_iface.json()["id"]

        mac_id = get_or_create_mac(iface.get("mac"))
        requests.patch(f"{NETBOX_URL}/dcim/mac-addresses/{mac_id}/", headers=HEADERS, verify=VERIFY_SSL, json={"assigned_object_id": iface_id, "assigned_object_type": "virtualization.vminterface"})
        requests.patch(f"{NETBOX_URL}/virtualization/interfaces/{iface_id}/", headers=HEADERS, verify=VERIFY_SSL, json={"primary_mac_address": mac_id})

        ip_check = requests.get(f"{NETBOX_URL}/ipam/ip-addresses/?assigned_object_id={iface_id}", headers=HEADERS, verify=VERIFY_SSL)
        existing_ips = {ip["address"].split("/")[0] for ip in ip_check.json()["results"]}

        for ip_entry in iface.get("ip_addresses", []):
            ip_addr = ip_entry["ip"]
            prefix = ip_entry["prefix"]
            full_ip = f"{ip_addr}/{prefix}"

            if ip_addr in existing_ips:
                continue

            ip_payload = {
                "address": full_ip,
                "assigned_object_type": "virtualization.vminterface",
                "assigned_object_id": iface_id,
                "status": "dhcp"
            }

            if iface_name == "docker0":
                vrf_name = f"{vm['name']}-docker"
                ip_payload["vrf"] = create_or_get_vrf(vrf_name)
                requests.patch(f"{NETBOX_URL}/virtualization/interfaces/{iface_id}/", headers=HEADERS, verify=VERIFY_SSL, json={"vrf": ip_payload["vrf"]})

            requests.post(f"{NETBOX_URL}/ipam/ip-addresses/", headers=HEADERS, verify=VERIFY_SSL, json=ip_payload)
