FROM centos:7

RUN cd /etc/yum.repos.d/ && \
    curl -LO https://fedorapeople.org/groups/virt/virtio-win/virtio-win.repo && \
    curl -LO http://file.brq.redhat.com/~tgolembi/repos/libguestfs-1.38.2-12.29.lp.el7ev+json.1/libguestfs.repo

RUN yum -y install \
        epel-release \
        centos-release-qemu-ev \
    && \
    yum -y update && \
    yum -y install \
        nbdkit \
        nbdkit-plugin-python2 \
        nbdkit-plugin-vddk \
        qemu-kvm-ev \
        python-six \
        virt-v2v \
        virtio-win \
    && \
    yum clean all

# The container image cannot handle sparse file properly. Keep --size to the
# required minimum.
RUN /usr/bin/supermin5 \
        --build \
        --verbose \
        --copy-kernel \
        --format ext2 \
        --size 300M \
        --host-cpu x86_64 \
        /usr/lib64/guestfs/supermin.d \
        -o /usr/lib64/guestfs/ && \
    touch /usr/lib64/guestfs/README.fixed && \
    LIBGUESTFS_BACKEND=direct libguestfs-test-tool

RUN install --mode=0775 --group=0 -d /data && \
    install --mode=0775 --group=0 -d /data/input && \
    install --mode=0775 --group=0 -d /data/vm && \
    install --mode=0775 --group=0 -d /opt/vmware-vix-disklib-distrib && \
    true

COPY entrypoint /usr/local/bin/entrypoint
# TODO: use RPM
COPY virt-v2v-wrapper.py /usr/local/bin/virt-v2v-wrapper

VOLUME ["/opt/vmware-vix-disklib-distrib"]
ENTRYPOINT ["/usr/local/bin/entrypoint"]
USER ${USER_UID}
