# Ansible Roles

There are many variables that can be configured to tune the setup.
Some are useful only in development environment and some are not really meant
to be changed.

| Variable                       | Default value | Description                                                                                                                   |
| -----------------------------  | ------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| v2v_host_type                  |               | Platform where the conversion host is deployed. Valid values: `rhevm`, `openstack`.                                           |
| v2v_transport_method           |               | Transport method to configure on the conversion host. Valid values: `vddk`, 'ssh`.                                            |
| v2v_vddk_package_url           |               | URL to the VDDK library package.                                                                                              |
| v2v_vddk_override              | false         | Normally the install role is not run if the plugin is already installed. To force the deployment set this variable to `true`. |
| v2v_ssh_private_key            |               | The private key to use to connect to the VMware host.                                                                         |
| v2v_ca_bundle                  |               | A bundle of CA certificates to allow connection to the provider where the conversion host belongs. See below for value.       |
| v2v_checks_override            | false         | The install role does performs some compatibility checks. By setting `v2v_checks_override` to `true` one can disable them.    |
| v2v_yum_check                  | latest        | Can be used to change the requirement on installed packages. Normally we check if the installed packages are at the latest version. This can cause troubles on disconnected or unconfigured systems. In that case the check can be ... by setting the value to `present`. (Since 1.7) |

## CA Bundle

For Red Hat Virtualization, the content of the CA chain is available in
`/etc/pki/ovirt-engine/apache-ca.pem` on the RHV Manager.

For OpenStack, the content of the CA chain can be built from the
undercloud CA (/etc/pki/ca-trust/source/anchors/undercloud-cacert.pem) and
the overcloud CA (/etc/pki/ca-trust/source/anchors/overcloud-cacert.pem).


## Example inventory

```yaml
all:
  vars:
  hosts:
    chost1.example.com:
      v2v_host_type: rhevm
      v2v_transport_method: vddk
    chost2.example.com:
      v2v_host_type: openstack
      v2v_transport_method: ssh
  vars:
    v2v_vddk_package_url: "http://content.example.com/VMware-vix-disklib-6.5.2-6195444.x86_64.tar.gz"
    v2v_ssh_private_key: |
      -----BEGIN RSA PRIVATE KEY-----
      b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAlwAAAAdzc2gtcn
      NhAAAAAwEAAQAAAIEAuUPezWEyOS+KbDj+GWjvQUjgugMvPREhHsTuqhvNs3rU7qkQYit6
      y8J5PFiuQAViDNvhjyESBh1QwT45utZUkUD0wrHJ/lrxmM9NKaDaYr/rNM97Gkao8Jp9aq
      /1IfQXYrlxIcdOpTTQRIib1f0QR1Rgo7Ekz0tNXvt3AXbqo98AAAIYq1X3AatV9wEAAAAH
      c3NoLXJzYQAAAIEAuUPezWEyOS+KbDj+GWjvQUjgugMvPREhHsTuqhvNs3rU7qkQYit6y8
      J5PFiuQAViDNvhjyESBh1QwT45utZUkUD0wrHJ/lrxmM9NKaDaYr/rNM97Gkao8Jp9aq/1
      IfQXYrlxIcdOpTTQRIib1f0QR1Rgo7Ekz0tNXvt3AXbqo98AAAADAQABAAAAgQCBjwQVrn
      4X3bY4vpZ8IJUIm7WEf8ueMgduZBvfXDg65pBYImTxsiRasDJmUEHzRZBvG6melWrsWb3q
      leB7V32lMNxXmFAORELLjo0LQUIROH+YjETxmEzaAvGK/PfNDTXuTKFlRp2+VMJIF+S0V/
      S4AsJ6YZkxH78RoexiYHFYMQAAAEEAtGPkFquU/Qy4POAf9HOb4Xe+dgMgENs+rZV3gzeD
      7wnQP1M7sZwGKhde+BlhiuSgkUW6+2Am/ui7nvOwt+9begAAAEEA7r1VsA+y7tljxwHWYT
      8lx5NIfFCfIaB3VpvlBltBxI0T56qMBxVIPoEgCcFL3CVtRLZ/KukgJKiXEk/EREgNFwAA
      AEEAxqjQUreggg6tzLrrDOchATWDxZH/KBpOpalrWc9afbDAbiOWidR9lex+X+pXHa1kYM
      ++vZcXPGeWRqLYHReseQAAAB9mZHVwb250QHNhbWFlbC5ob21lLmV2ZW5pdC5pbmZvAQI=
      -----END RSA PRIVATE KEY-----
    v2v_ca_bundle: |
      -----BEGIN TRUSTED CERTIFICATE-----
      MIIDNzCCAh8CAQEwDQYJKoZIhvcNAQELBQAwYjELMAkGA1UEBhMCVVMxCzAJBgNV
      BAgMAk5DMRAwDgYDVQQHDAdSYWxlaWdoMRAwDgYDVQQKDAdSZWQgSEF0MQswCQYD
      VQQLDAJRRTEVMBMGA1UEAwwMMTkyLjE2OC4yNC4yMB4XDTE4MTEwODIyMzg0MVoX
      DTE5MTEwODIyMzg0MVowYTELMAkGA1UEBhMCVVMxCzAJBgNVBAgMAk5DMRAwDgYD
      VQQHDAdSYWxlaWdoMRAwDgYDVQQKDAdSZWQgSEF0MQswCQYDVQQLDAJRRTEUMBIG
      A1UEAwwLMTAuOC4xOTcuODQwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIB
      AQDCh0IIszxUjiR5YiBZDzYWFKircfiKXgw2o9K5V6J5Aqflnm2tUHl1jtucAwsk
      EoDIgc9Cf8UVS8gw5KBIFxJKnILhu1HGud/jrNtNZuBOq1WeMa8suSKSOg1tvH+k
      ltbzLMBFBz1x/AnzUkadpQQeaw58pP8kQIT/MTkw9i+yEwq2tjwer+806tWMpm0e
      eG27UbCVpel/ex6WTR5sUe0lmoRoVwpBkC0WQsip9Ly8aY0ZeHgnWIeNvf52olwW
      hEl7LhpRMUH0E24uEAo7+ChiNp7q640L0QWcEDfTYEwTyS+zy1/n0S0EWohWLyxR
      B7GDr+4z+dgztGCjKtKmTN2fAgMBAAEwDQYJKoZIhvcNAQELBQADggEBAIBMl35D
      onOMr1ZuKFMnl/x3LhJihRL5c1XZ2VTPx7P6fOeEoWwocuqW40BE+HLMXX9K4dUI
      fYEi/vRSh/8Obcirvobztl2KPripo1PXOLx82a8eTpQFubELqBKVVSUQkIIKpyIW
      Itbf/+4I08j9hXG1XGZtla05SEx9je5ntZI9DwsNRIe3ZNWeEoTZnG5cpXKoTuiv
      ZSBZV5uygZ6yGv7hnoqVRNXZP4OKE0ZdVt1TxbO0dBPjav6NdTpi7e9ZmVGKv9Xi
      drf/14FoGeDsU2zXLQ/UAqlzaAqx1NAtp99wnX3yI2dXJzGpVXdl0SJU6Hi5M+32
      PFjCzC1Et4Yl6sUwDQwLMTAuOC4xOTcuODQ=
      -----END TRUSTED CERTIFICATE-----
      -----BEGIN TRUSTED CERTIFICATE-----
      MIIDlzCCAn+gAwIBAgIJAOP7AaT7dsLYMA0GCSqGSIb3DQEBCwUAMGIxCzAJBgNV
      BAYTAlVTMQswCQYDVQQIDAJOQzEQMA4GA1UEBwwHUmFsZWlnaDEQMA4GA1UECgwH
      UmVkIEhBdDELMAkGA1UECwwCUUUxFTATBgNVBAMMDDE5Mi4xNjguMjQuMjAeFw0x
      ODExMDUyMTE3NTFaFw0xOTExMDUyMTE3NTFaMGIxCzAJBgNVBAYTAlVTMQswCQYD
      VQQIDAJOQzEQMA4GA1UEBwwHUmFsZWlnaDEQMA4GA1UECgwHUmVkIEhBdDELMAkG
      A1UECwwCUUUxFTATBgNVBAMMDDE5Mi4xNjguMjQuMjCCASIwDQYJKoZIhvcNAQEB
      BQADggEPADCCAQoCggEBAODRYemzQhGx2+8t0Peru6BEv2EFpxGKbav5p+6NzFXb
      50ccUIXA59+5vEEUr8EF8aCJuizBjAskPXwAT89qlbrsxKfN/r/xFOGvMkQ3xA2S
      ucnZCZXaVGkk/KC3VzPrd3atmPHWmAjTb37m4b1vKBRC9zh1F1l2CEyb31Eku4br
      gi4PwqoUQWIwiXPhD88YLuRKxdc079j7NRICBfJN68tzK81TW9cCQiOR7PdMuPzm
      h9nyNfYDeuGOuIFbpmL+8cLCofdMlyKtz/v+Y8s5bTtEf0ETG3qLYXskWM5nYYK+
      AkdKV3yZDrBha1X9Qo8wRcNZUas0kycXtZOXspPQ9AkCAwEAAaNQME4wHQYDVR0O
      BBYEFJzqK4CnpGbnQJIsN2te/jndZk2yMB8GA1UdIwQYMBaAFJzqK4CnpGbnQJIs
      N2te/jndZk2yMAwGA1UdEwQFMAMBAf8wDQYJKoZIhvcNAQELBQADggEBAJs+irLk
      osXnAqN8HwCp8NFGo2armdb/HE7v+qUanHRFfxDJJ70KhTM2gEi732u19oxhpgJP
      LNj40fC6U6A17oeNzGk+U75cOnbHY0Wovdo/2E8n508zsg0f+h8170QCKwf1qqd+
      o+AbxDIH6C262pF4AGjYQxK302Xj4Te+XckQa6nIX4xk1xJeHEzlxfBcV3h6BQH8
      sVqekffwgMFam9A66Ovcx8QgzZ2HpVnuq/CMY/sUxp0dK5PsnpKbUm6UCqaXigY7
      hgpqdqIdwkeR+c+fbYZKXBOBotCcmEXoHuIlZ9GhIti7gwSBSRWEjkPEL2j8R/zK
      k2ikyNbbVRx/13AwDgwMMTkyLjE2OC4yNC4y
      -----END TRUSTED CERTIFICATE-----
```
