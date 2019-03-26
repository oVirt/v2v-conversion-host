#!/bin/bash -xe
cd "$(dirname "$0")"
cp ../wrapper/virt-v2v-wrapper.py .
chmod a+rx virt-v2v-wrapper.py
docker build -t quay.io/nyoxi/kubevirt-conversion-pod:latest .
