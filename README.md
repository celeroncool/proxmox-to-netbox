# Proxmox to Netbox sync scripts

This repository contains scripts for synchronizing VMs and LXCs data from Proxmox VE to NetBox Virtual Machines.

`proxmox_export.py` generates a JSON file `proxmox_vms.json` containing Proxmox VE VMs and LXCs.

`netbox_import.py proxmox_vms.json` imports data from the JSON file into NetBox. Existing Virtual Machines in NetBox will be updated.

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
python ./netbox_import.py proxmox_vms.json
```

## Imported data

```jsonc
[
  {
    "name": "nextcloud", // vm/lxc name from proxmox for creating Virtual Machine in NetBox
    "type": "qemu", // Virtualization type, maps to "platform" in netbox
    "ostype": null, // null for VM, ostype for LXC. Will create a Platform in NetBox if not exists
    "vcpu": 2, // cpu count
    "ram_mb": "16384", // memory in MB
    "disks": [ // all virtual machine disks
      {
        "name": "scsi0",
        "size_gb": 40,
        "description": "local-zfs:vm-600-disk-1,cache=writethrough,discard=on,iothread=1,size=40G,ssd=1"
      }
    ],
    "interfaces": [ // interface names starting with br-/lo/Loopback/veth/docker_/tun are ignored
      {
        "name": "ens18", // interface name inside VM/LXC. Will create a interface in NetBox if not exists and link to VM
        "mac": "bc:24:11:37:a6:6a", // mac address. Will create a mac address in NetBox if not exists and link to interface and it IP addresses
        "ip_addresses": [
          {
            "ip": "192.168.42.25",
            "prefix": 25
          },
          {
            "ip": "dead:b33f:a1a1:fa04:be24:11ff:fe11:a563", // ignores link-local addresses
            "prefix": 64
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
    "disks": [
      {
        "name": "rootfs",
        "size_gb": 20,
        "description": "local-zfs:subvol-109-disk-0,size=20G"
      }
    ],
    "interfaces": [
      {
        "name": "eth0",
        "mac": "bc:24:11:3c:e2:6c",
        "ip_addresses": [] // Only includes IP addresses if set manually in config.
      },
      {
        "name": "enp0s18",
        "mac": "02:49:15:03:3c:e4",
        "ip_addresses": [
          {
            "ip": "192.168.10.22",
            "prefix": 24
          }
        ]
      }
    ],
    "host": "pve-01"
  }
]
```

## License

[MIT](https://choosealicense.com/licenses/mit/)
