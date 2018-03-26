# Virt-v2v Wrapper

The script shields the caller from complexities involved in starting virt-v2v
on oVirt/RHV host. It daemonizes to the background and monitors the progress
of the conversion, providing the status information in a state file. This
allows for asynchronous conversion workflow.

The expected usage is as follows:

1)  *wrapper start*: client runs the wrapper; at the moment there are no
    command line arguments and everything is configured by JSON data presented
    on stdin.

2)  *initialization*: wrapper read JSON data from stdin, parses and validates
    the content; based on the situation it may also change the effective user
    to a non-root account

3)  *daemonization*: wrapper writes to stdout simple JSON containing paths to
    wrapper log file (`wrapper_log`), virt-v2v log file (`v2v_log`) and to the
    state file (`state_file`) that can be used to monitor the progress; after
    that it forks to the background

4)  *conversion*: finally, virt-v2v process is executed; wrapper monitors its
    output and updates the state file on a regular basis

5)  *finalization*: when virt-v2v terminates wrapper updates the state file
    one last time and exits


## Input Data

This section describes various keys understood by the wrapper in JSON on
input. Keys are mandatory unless explicitly stated otherwise.

General information:

* `vm_name`: name of the VM to import

* `output_format`: one of `raw`, or `qcow2`; default is `raw` if not specified

Source configuration:

* `transport_method`: type of transport to use; for now only `vddk` is
  supported

* `vmware_uri`: libvirt URI of the source hypervisor

* `vmware_password`: password used when connecting to the source hypervisor

* `vmware_fingerprint`: fingerprint of SSL certificate on the source
  hypervisor (also called thumbprint)

Output configuration:

* `export_domain`: location of the oVirt NFS export domain;

Miscellaneous:

* `source_disks`: optional key containing list of disks in the VM; if specified
  it is used to initialize progress information in the state file

Example:

    {
        "export_domain": "storage1.example.com:/data/export-domain",
        "vm_name": "My_Machine",

        "transport_method": "vddk",
        "vmware_fingerprint": "1A:3F:26:C6:DC:2C:44:88:AA:33:81:3C:18:6E:5D:9F:C0:EE:DF:5C",
        "vmware_uri": "esx://root@10.2.0.20?no_verify=1",
        "vmware_password": "secret-password",

        "source_disks": [
            "[dataStore_1] My_Machine/My_Machine_1.vmdk",
            "[dataStore_1] My_Machine/My_Machine_2.vmdk"
        ]
    }


## State File Format

State file is a JSON file. Its content changes as the conversion goes through
various stages. With it also the keys present in the file.

Once virt-v2v is executed the state file is created with the following keys:

* `started`: with value `true`

* `pid`: the process ID of virt-v2v. This can be used to kill the process and
  terminate the conversion. In this case, once virt-v2v terminates (with
  non-zero return code) the wrapper immediately terminates too.

* `disks`: array of progress per each disk. The value is either empty list or
  a list of objects initialized from `source_disks` passed to the wrapper. If
  no `source_disks` is specified, the `disks` list is constructed incrementally
  during the conversion process.

* `disk_count`: the number of disks that will be copied. Initially zero or
  number of disks in `source_disks`. When virt-v2v starts copying disks, the
  value is updated to match the count of disks virt-v2v will actually copy.
  Note that the values does not have to represent the length of `disks` array!
  If `source_disks` is not specified or contains invalid values length of
  `disks` can be smaller or larger than `disk_count`.

When virt-v2v gets past the initialization phase and starts copying disks the
wrapper updates the progress for each disk in the `disks` list. Each item in
the list contains the following keys:

* `path`: the path description of the disk as the backend sees it

* `progress`: the percentage of the disk copied, in the range from 0 to 100 (as
  numeric)

When virt-v2v finishes the state is updated with the following keys:

* `return_code`: return code of virt-v2v process. As usual 0 means the process
  terminated successfully and any non-zero value means an error. Note however,
  that the value should not be used to check if conversion succeeded or failed.
  (See below.)

Right before the wrapper terminates it updates the state with:

* `finished`: with value `true`

* `failed`: with value `true` if the conversion process failed. If everything
  went OK, this key is not present. Existence of this key is the main way how
  to check whether the conversion succeeded or not.
