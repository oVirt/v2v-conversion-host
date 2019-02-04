# vim: ft=dockerfile
FROM centos:7

LABEL maintainer="Tomas Golembiovsky <tgolembi@redhat.com>"

RUN yum -y update && \
    yum -y install epel-release && \
    yum -y install golang && \
    yum clean all

RUN mkdir -p /opt/kubevirt-provider/bin
RUN mkdir -p /go/src/github.com/ovirt/v2v-conversion-host/
ADD . /go/src/github.com/ovirt/v2v-conversion-host/kubevirt-provider
WORKDIR /go/src/github.com/ovirt/v2v-conversion-host/kubevirt-provider
RUN GOPATH=/go go build -o /opt/kubevirt-provider/bin/kubevirt-provider .

# doesn't require a root user.
USER 1001
EXPOSE 8080/tcp

CMD /opt/kubevirt-provider/bin/kubevirt-provider
