#!/bin/bash

RPM_VERSION="1.12.0"
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

  mkdir -p "$AUX_DATA_DIR/playbooks"
  install -t "$AUX_DATA_DIR/playbooks" ansible/examples/*.yml

  echo "Installation done."
}

do_$1
