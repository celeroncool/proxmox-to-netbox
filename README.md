# Proxmox to Netbox sync scripts

This repository contains scripts for synchronizing VMs and LXCs data from Proxmox VE to NetBox Virtual Machines.

`proxmox_export.py` generates a JSON file containing Proxmox VE VMs and LXCs.

`netbox_import.py` imports data from the JSON file into NetBox. Existing Virtual Machines in NetBox will be updated.

## Setup

### Virtual Environment (Optional)

Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows, use: venv\Scripts\activate
```

### Install Dependencies

Install required packages:

```bash
pip install -r requirements.txt
```

### Environment Configuration

Create a `.env` file from the example:

```bash
cp example.env .env
```

Edit the `.env` file with your configuration values.

## Running the Application

Ensure that you created a Device in NetBox for the Proxmox node.

Run the `proxmox_export.py` script:

```bash
python ./proxmox_export.py
```

Check the output file `proxmox_vms.json`.

Run the `netbox_import.py` script:

```bash
python ./netbox_import.py
```

## Imported data

```jsonc
[
  {
    "name": "nextcloud", // vm/lxc name from proxmox for creating Virtual Machine in NetBox
    "type": "qemu", // not used
    "ostype": null, // null for VM, ostype for LXC. Will create a Platform in NetBox if not exists
    "vcpu": 2, // cpu count
    "ram_mb": "16384", // memory in MB
    "disk_gb": 52, // disk size in GB
    "interfaces": [ // interface names starting with br-/lo/Loopback/veth/docker_/tun are ignored
      {
        "name": "ens18", // interface name inside VM/LXC. Will create a interface in NetBox if not exists and link to VM
        "mac": "bc:24:11:37:a6:6a", // mac address. Will create a mac address in NetBox if not exists and link to interface and it IP addresses
        "ip_addresses": [
          {
            "ip": "192.168.88.11", // only ipv4 is exported. 127.0.0.0/8 is ignored
            "prefix": 24
          }
        ]
      },
      {
        "name": "docker0", // for docker0 a VRF is always created (to prevent "ip duplicate" errors)
        "mac": "02:42:b7:4a:fb:57",
        "ip_addresses": [
          {
            "ip": "172.17.0.1",
            "prefix": 16
          }
        ]
      }
    ],
    "host": "pve-01" // Proxmox node name, used for linking Virtual Machine to Device in NetBox
  },
  {
    "name": "jellyfin",
    "type": "lxc",
    "ostype": "ubuntu",
    "vcpu": 2,
    "ram_mb": 2048,
    "disk_gb": 28,
    "interfaces": [
      {
        "name": "eth0",
        "mac": "bc:24:11:3c:e2:6c",
        "ip_addresses": [] // ip addresses for LXC can't be obtained from Proxmox via API
      }
    ],
    "host": "pve-01"
  }
]
```

## License

[MIT](https://choosealicense.com/licenses/mit/)
