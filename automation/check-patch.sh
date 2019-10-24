#!/bin/bash -xe

# remove any previous artifacts
rm -rf output
rm -f ./*tar.gz

# Get the tarball
./build.sh dist

# create the src.rpm
rpmbuild \
    -D "_srcrpmdir $PWD/output" \
    -D "_topmdir $PWD/rpmbuild" \
    -ts ./*.gz

# install any build requirements
DISTVER="$(rpm --eval "%dist"|cut -c2-4)"
if [[ "${DISTVER}" == "el7" ]]; then
    yum-builddep -y output/*src.rpm
else
    dnf builddep -y output/*src.rpm
fi


# create the rpms
rpmbuild \
    -D "_rpmdir $PWD/output" \
    -D "_topmdir $PWD/rpmbuild" \
    --rebuild output/*.src.rpm

# Store any relevant artifacts in exported-artifacts for the ci system to
# archive
[[ -d exported-artifacts ]] || mkdir -p exported-artifacts
find output -iname \*rpm -exec mv "{}" exported-artifacts/ \;
mv *.tar.gz exported-artifacts
