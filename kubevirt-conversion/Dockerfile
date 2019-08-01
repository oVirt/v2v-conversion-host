FROM centos:7 as common

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


FROM common as builder

RUN yum -y install \
  python-devel \
  python-setuptools

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

# TODO: use RPM
RUN cd /source && \
  python setup.py install --record /tmp/filelist.txt --skip-build && \
  tar -cPf /tmp/wrapper_output.tar --no-unquote --files-from=/tmp/filelist.txt
# Ideally use --verbatim-files-from, but tar from centos-7 does not know that.

FROM common

COPY --from=builder /usr/lib64/guestfs/ /usr/lib64/guestfs/

RUN install --mode=0775 --group=0 -d /data && \
    install --mode=0775 --group=0 -d /data/input && \
    install --mode=0775 --group=0 -d /data/vddklib && \
    install --mode=0775 --group=0 -d /data/vm && \
    ln -s /data/vddklib/vmware-vix-disklib-distrib /opt/vmware-vix-disklib-distrib &&\
    true

COPY --from=builder /tmp/wrapper_output.tar /tmp/wrapper_output.tar
RUN tar xvf /tmp/wrapper_output.tar

COPY entrypoint /usr/local/bin/entrypoint

VOLUME ["/opt/vmware-vix-disklib-distrib"]
ENTRYPOINT ["/usr/local/bin/entrypoint"]
USER ${USER_UID}
