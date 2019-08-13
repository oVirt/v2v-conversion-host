#!/bin/bash -e

RPM_VERSION="1.15.0"
KUBEVIRT_VERSION="2.0.0"
KUBEVIRT_CONVERSION_RELEASE="2"
KUBEVIRT_VMWARE_RELEASE="2"

QUAY_NS=quay.io/kubevirt


if git describe --exact-match --tags --match "v[0-9]*" > /dev/null 2>&1 ; then
    RPM_RELEASE="1"
else
    GIT="$(
        git describe --always --tags --match "v[0-9]*" --dirty=.dr |
        sed -r 's/^/git/; s/^[^-]*-//; s/-g/.git/; s/-/_/g'
    )"
    RPM_RELEASE="0.$GIT.$(date -u +%Y%m%d%H%M%S)"
fi

ROLE_NAME="oVirt.v2v-conversion-host"
PACKAGE_NAME="v2v-conversion-host"
ROLE_RPM_NAME="${PACKAGE_NAME}-ansible"

PREFIX="${PREFIX:-/usr/local}"
DATA_DIR="${DATA_DIR:-${PREFIX}/share}"
BIN_DIR="${BIN_DIR:-${PREFIX}/bin}"

ROLES_DIR="$DATA_DIR/ansible/roles"
AUX_DATA_DIR="$DATA_DIR/$ROLE_RPM_NAME"

TARBALL="$PACKAGE_NAME-$RPM_VERSION.tar.gz"

do_dist() {
  echo "Creating tar archive '$TARBALL' ... "
  sed \
   -e "s|@RPM_VERSION@|$RPM_VERSION|g" \
   -e "s|@RPM_RELEASE@|$RPM_RELEASE|g" \
   -e "s|@PACKAGE_NAME@|$PACKAGE_NAME|g" \
   < "$PACKAGE_NAME.spec.in" > "$PACKAGE_NAME.spec"

  git ls-files | tar --files-from /proc/self/fd/0 -czf "$TARBALL" "$PACKAGE_NAME.spec"
  echo "tar archive '$TARBALL' created."
}

do_install() {
  echo "Installing data..."

  mkdir -p $ROLES_DIR
  cp -pR "ansible/$ROLE_NAME" "$ROLES_DIR"

  mkdir -p $BIN_DIR
  install --mode=0755 -T wrapper/virt-v2v-wrapper.py $BIN_DIR/virt-v2v-wrapper
  sed -i "1s#/usr/bin/python2\$#$PYTHON#" $BIN_DIR/virt-v2v-wrapper

  mkdir -p "$AUX_DATA_DIR/playbooks"
  install -t "$AUX_DATA_DIR/playbooks" ansible/examples/*.yml

  echo "Installation done."
}

do_build_conversion() {
    TAG="v$KUBEVIRT_VERSION-$KUBEVIRT_CONVERSION_RELEASE"
    IMAGE="$QUAY_NS/kubevirt-v2v-conversion"

    # TODO: make sure the TAG is not used yet to avoid overwrite

    pushd kubevirt-conversion
    # TODO: use RPM with wrapper
    cp ../wrapper/virt-v2v-wrapper.py .
    chmod a+rx virt-v2v-wrapper.py
    docker build -t "$IMAGE:$TAG" .
    rm virt-v2v-wrapper.py
    popd

    # TODO: When to tag as 'latest'? Do it manualy for now.
    #docker push quay.io/nyoxi/kubevirt-conversion:latest
}

do_build_vmware() {
    TAG="v$KUBEVIRT_VERSION-$KUBEVIRT_CONVERSION_RELEASE"
    IMAGE="$QUAY_NS/kubevirt-vmware"

    # Prepare golang environment
    pushd kubevirt-vmware > /dev/null
    export GOPATH="$(pwd)/build/GOPATH"
    if [ -e "$GOPATH" ] ; then
        echo "GOPATH exists ($GOPATH)" >&2
        echo "Remove it and try again" >&2
        exit 1
    fi
    IPATH="$GOPATH/src/github.com/ovirt/v2v-conversion-host/"
    mkdir -p "$IPATH"
    pushd $IPATH > /dev/null
    ln -s $(dirs +2)/kubevirt-vmware
    cd kubevirt-vmware

    # Build operator
    operator-sdk build "$IMAGE:$TAG"

    # Drop out and clean
    popd > /dev/null # $IPATH/kubevirt-vmware
    popd > /dev/null # /kubevirt-vmware
    rm -frv "$GOPATH"
}

do_images() {
    do_build_conversion
    do_build_vmware
}

do_$1
