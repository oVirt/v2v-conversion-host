# Ansible Roles

There are many variables that can be configured to tune the setup.
Some are useful only in development environment and some are not really meant
to be changed.

The onely one that has to be defined is:

* `v2v_vddk_package_name`: this is the file name of the .tar.gz package with
  VDDK library. It is looked for in `/tmp` directory.

If you don't have the package locally on the host, you can also specify:

* `v2v_vddk_package_url`: the URL to the VDDK library package. The package is
  first downloaded from this location and stored in `/tmp`. Note that the file
  name must much the one in `v2v_vddk_package_name`. The best thing to achieve
  that is to incorporate that in to the URL, e.g.:
  `v2v_vddk_package_url: "http://gateway/vddk/{{ v2v_vddk_package_name }}"`

Other interesting variables are:

* `v2v_vddk_override`: normally the install role is not run if the plugin is
  already installed. To force the deployment set this variable to `true`.

* `v2v_checks_override`: the install role does performs some compatibility
  checks. By setting `v2v_checks_override` to `true` one can disable them.

* `v2v_yum_check` -- (default: `latest`) can be used to change the requirement
  on installed packages. Normally we check if the installed packages are at the
  latest version. This can cause troubles on disconnected  or unconfigured
  systems. In that case the check can be ... by setting the value to `present`.
  (Since 1.7)

## To be documented...

* `v2v_repo_rpms_name`, `v2v_repo_rpms_url`, `v2v_repo_srpms_name`,
  `v2v_repo_srpms_url`:

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
