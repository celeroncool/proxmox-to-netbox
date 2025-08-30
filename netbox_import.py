#!/usr/bin/env python3

import json
import os
import sys
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from dotenv import load_dotenv

load_dotenv()

# Disable SSL warnings if needed
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# NetBox configuration
NETBOX_URL = os.getenv('NETBOX_URL', 'https://netbox.example.com')
NETBOX_TOKEN = os.getenv('NETBOX_TOKEN')
CLUSTER_ID = os.getenv('CLUSTER_ID')
VERIFY_SSL = os.getenv('VERIFY_SSL', 'true').lower() == 'true'

if not NETBOX_TOKEN:
    print("ERROR: NETBOX_TOKEN environment variable is required")
    sys.exit(1)

if not CLUSTER_ID:
    print("ERROR: CLUSTER_ID environment variable is required")
    sys.exit(1)

headers = {
    'Authorization': f'Token {NETBOX_TOKEN}',
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

def netbox_request(method, endpoint, data=None):
    """Make a request to NetBox API"""
    url = f"{NETBOX_URL}/api/{endpoint}"

    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, verify=VERIFY_SSL)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data, verify=VERIFY_SSL)
        elif method == 'PATCH':
            response = requests.patch(url, headers=headers, json=data, verify=VERIFY_SSL)
        elif method == 'PUT':
            response = requests.put(url, headers=headers, json=data, verify=VERIFY_SSL)

        response.raise_for_status()
        return response.json() if response.content else None
    except requests.exceptions.RequestException as e:
        print(f"ERROR: NetBox API request failed: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return None

def verify_cluster():
    """Verify that the cluster ID exists"""
    cluster = netbox_request('GET', f'virtualization/clusters/{CLUSTER_ID}/')
    if cluster:
        print(f"INFO: Using cluster: {cluster['name']} (ID: {CLUSTER_ID})")
        return True
    else:
        print(f"ERROR: Cluster with ID {CLUSTER_ID} not found")
        return False

def get_or_create_platform(platform_name):
    """Get or create a platform in NetBox"""
    # First, try to find existing platform
    platforms = netbox_request('GET', f'dcim/platforms/?name={platform_name}')
    if platforms and platforms['results']:
        platform_id = platforms['results'][0]['id']
        print(f"INFO: Found existing platform: {platform_name} (ID: {platform_id})")
        return platform_id

    # If platform doesn't exist, create it
    platform_data = {
        'name': platform_name,
        'slug': platform_name.lower()
    }

    platform = netbox_request('POST', 'dcim/platforms/', platform_data)
    if platform:
        platform_id = platform['id']
        print(f"INFO: Created platform: {platform_name} (ID: {platform_id})")
        return platform_id
    else:
        print(f"ERROR: Failed to create platform: {platform_name}")
        return None

def import_vm(vm_data):
    """Import a single VM into NetBox"""
    vm_name = vm_data['name']
    host_name = vm_data['host']
    vm_type = vm_data.get('type', 'unknown')

    print(f"INFO: Processing VM: {vm_name} from host: {host_name} (type: {vm_type})")

    # Get or create platform based on VM type
    platform_id = None
    if vm_type in ['qemu', 'lxc']:
        platform_id = get_or_create_platform(vm_type)

    # Handle offline VMs
    if vm_data.get('status') == 'offline':
        print(f"INFO: VM {vm_name} is offline, creating basic entry")
        vm_payload = {
            'name': vm_name,
            'status': 'offline',
            'cluster': int(CLUSTER_ID),
            'comments': f"Imported from Proxmox host: {host_name}"
        }
        if platform_id:
            vm_payload['platform'] = platform_id
    else:
        # Create VM payload for running VMs
        vm_payload = {
            'name': vm_name,
            'status': 'active',
            'vcpus': vm_data.get('vcpu', 1),
            'memory': vm_data.get('ram_mb', 0),
            'cluster': int(CLUSTER_ID),
            'comments': f"Imported from Proxmox host: {host_name}. Type: {vm_data.get('type', 'unknown')}"
        }

        if platform_id:
            vm_payload['platform'] = platform_id

        if vm_data.get('ostype'):
            vm_payload['comments'] += f", OS: {vm_data['ostype']}"

    # Check if VM already exists
    existing_vms = netbox_request('GET', f'virtualization/virtual-machines/?name={vm_name}')
    if existing_vms and existing_vms['results']:
        vm_id = existing_vms['results'][0]['id']
        vm = netbox_request('PATCH', f'virtualization/virtual-machines/{vm_id}/', vm_payload)
        print(f"INFO: Updated VM: {vm_name}")
    else:
        vm = netbox_request('POST', 'virtualization/virtual-machines/', vm_payload)
        print(f"INFO: Created VM: {vm_name}")

    if not vm:
        print(f"ERROR: Failed to create/update VM: {vm_name}")
        return False

    # Process disks for running VMs
    if vm_data.get('status') == 'running' and 'disks' in vm_data:
        for disk_data in vm_data['disks']:
            create_vm_disk(vm['id'], disk_data)

    # Process interfaces for running VMs
    if vm_data.get('status') == 'running' and 'interfaces' in vm_data:
        for interface_data in vm_data['interfaces']:
            create_vm_interface(vm['id'], interface_data)

    return True

def create_vm_disk(vm_id, disk_data):
    """Create VM disk in NetBox"""
    disk_name = disk_data['name']
    disk_size_gb = disk_data.get('size_gb', 0)
    disk_description = disk_data.get('description', '')

    print(f"INFO: Processing disk: {disk_name} ({disk_size_gb}GB)")

    # Create disk payload
    disk_payload = {
        'virtual_machine': vm_id,
        'name': disk_name,
        'size': disk_size_gb * 1024,  # Convert GB to MB for NetBox
        'description': disk_description
    }

    # Check if disk already exists
    existing_disks = netbox_request('GET', f'virtualization/virtual-disks/?virtual_machine_id={vm_id}&name={disk_name}')
    if existing_disks and existing_disks['results']:
        disk_id = existing_disks['results'][0]['id']
        disk = netbox_request('PATCH', f'virtualization/virtual-disks/{disk_id}/', disk_payload)
        if disk:
            print(f"INFO: Updated disk: {disk_name}")
        else:
            print(f"ERROR: Failed to update disk: {disk_name}")
    else:
        disk = netbox_request('POST', 'virtualization/virtual-disks/', disk_payload)
        if disk:
            print(f"INFO: Created disk: {disk_name}")
        else:
            print(f"ERROR: Failed to create disk: {disk_name}")

def create_vm_interface(vm_id, interface_data):
    """Create VM interface and IP addresses"""
    interface_name = interface_data['name']

    # Create interface
    interface_payload = {
        'virtual_machine': vm_id,
        'name': interface_name,
        'type': 'virtual'
    }

    if 'mac' in interface_data and interface_data['mac'] and interface_data['mac'] != '00:00:00:00:00:00':
        interface_payload['mac_address'] = interface_data['mac']

    # Check if interface exists
    existing_interfaces = netbox_request('GET', f'virtualization/interfaces/?virtual_machine_id={vm_id}&name={interface_name}')
    if existing_interfaces and existing_interfaces['results']:
        interface_id = existing_interfaces['results'][0]['id']
        interface = netbox_request('PATCH', f'virtualization/interfaces/{interface_id}/', interface_payload)
    else:
        interface = netbox_request('POST', 'virtualization/interfaces/', interface_payload)

    if not interface:
        print(f"ERROR: Failed to create interface: {interface_name}")
        return

    print(f"INFO: Created/updated interface: {interface_name}")

    # Create IP addresses
    for ip_data in interface_data.get('ip_addresses', []):
        create_ip_address(interface['id'], ip_data)

def create_ip_address(interface_id, ip_data):
    """Create IP address for interface"""
    ip_address = f"{ip_data['ip']}/{ip_data['prefix']}"

    print(f"INFO: Processing IP: {ip_address}")

    # Check if IP already exists
    existing_ips = netbox_request('GET', f'ipam/ip-addresses/?address={ip_address}')
    if existing_ips and existing_ips['results']:
        # Update existing IP
        ip_id = existing_ips['results'][0]['id']
        ip_payload = {
            'assigned_object_type': 'virtualization.vminterface',
            'assigned_object_id': interface_id
        }
        result = netbox_request('PATCH', f'ipam/ip-addresses/{ip_id}/', ip_payload)
        if result:
            print(f"INFO: Updated existing IP: {ip_address}")
        else:
            print(f"ERROR: Failed to update IP: {ip_address}")
    else:
        # Create new IP
        ip_payload = {
            'address': ip_address,
            'assigned_object_type': 'virtualization.vminterface',
            'assigned_object_id': interface_id,
            'status': 'active'
        }

        result = netbox_request('POST', 'ipam/ip-addresses/', ip_payload)
        if result:
            print(f"INFO: Created IP: {ip_address}")
        else:
            print(f"ERROR: Failed to create IP: {ip_address}")

def main():
    """Main import function"""
    if len(sys.argv) != 2:
        print("Usage: python netbox_import.py <json_file>")
        print("Required environment variables:")
        print("  NETBOX_URL - NetBox instance URL")
        print("  NETBOX_TOKEN - NetBox API token")
        print("  CLUSTER_ID - NetBox cluster ID to assign VMs to")
        sys.exit(1)

    json_file = sys.argv[1]

    if not os.path.exists(json_file):
        print(f"ERROR: File not found: {json_file}")
        sys.exit(1)

    # Verify cluster exists
    if not verify_cluster():
        sys.exit(1)

    try:
        with open(json_file, 'r') as f:
            vms_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON file: {e}")
        sys.exit(1)

    print(f"INFO: Importing {len(vms_data)} VMs into NetBox cluster {CLUSTER_ID}")

    success_count = 0
    for vm_data in vms_data:
        if import_vm(vm_data):
            success_count += 1

    print(f"INFO: Successfully imported {success_count}/{len(vms_data)} VMs")

if __name__ == '__main__':
    main()