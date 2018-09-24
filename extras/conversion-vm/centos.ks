lang en_US
keyboard us
timezone America/New_York --isUtc

rootpw --lock

#platform x86, AMD64, or Intel EM64T
reboot
text
cdrom
bootloader --location=mbr --append="rhgb quiet crashkernel=auto"
zerombr
clearpart --all --initlabel
autopart
auth --passalgo=sha512 --useshadow
selinux --enforcing
firewall --enabled --ssh
firstboot --disable

repo --name=base --baseurl=http://mirror.centos.org/centos/7/os/x86_64/
repo --name=epel --baseurl=http://dl.fedoraproject.org/pub/epel/7/x86_64/

%packages
# tasks/install.yml
nbdkit
nbdkit-plugin-python2
virt-v2v
%end

%post --log=/root/ks-post-v2v.log --erroronfail
echo "Installing oVirt repo.."
yum install -y http://resources.ovirt.org/pub/yum-repo/ovirt-release42.rpm

echo "Installing ovirt-ansible-v2v-conversion-host package.."
yum install -y ovirt-ansible-v2v-conversion-host
%end
