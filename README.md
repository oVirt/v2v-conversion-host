# V2V - Transformation Host - Ansible
This repository contains Ansible artefacts used for V2V Transformation Host

## Example inventory

```yaml
all:
  vars:
    v2v_repo_rpms_name: "v2v-nbdkit-rpms"
    v2v_repo_rpms_url: "http://content.example.com/v2v-nbdkit-rpms"
    v2v_repo_srpms_name: "v2v-nbdkit-src-rpms"
    v2v_repo_srpms_url: "http://content.example.com/v2v-nbdkit-src-rpms"
    v2v_vddk_package_name: "VMware-vix-disklib-6.5.2-6195444.x86_64.tar.gz"
    v2v_vddk_package_url: "http://content.example.com/VMware-vix-disklib-6.5.2-6195444.x86_64.tar.gz"
  hosts:
    rhvh01.example.com:
    rhvh02.example.com:
```
