To build the image with VDDK first untar the library into the directory with
Dockerfile and then build and push the image to internal registry.

```
$ VERSION=6.5.2-6195444
$ REGISTRY=docker-registry-default.cloudapps.example.com
$ tar xzf /data/vddk/VMware-vix-disklib-$VERSION.x86_64.tar.gz
$ docker build -t $REGISTRY/kubevirt/vddk:$VERSION .
Sending build context to Docker daemon  70.96MB
Step 1/4 : FROM busybox:latest
latest: Pulling from library/busybox
fc1a6b909f82: Pull complete
Digest: sha256:954e1f01e80ce09d0887ff6ea10b13a812cb01932a0781d6b0cc23f743a874fd
Status: Downloaded newer image for busybox:latest
 ---> af2f74c517aa
Step 2/4 : COPY vmware-vix-disklib-distrib /
 ---> c279eda0ae8f
Step 3/4 : RUN mkdir -p /opt
 ---> Running in 4348830701ef
Removing intermediate container 4348830701ef
 ---> 9a29d5a92155
Step 4/4 : ENTRYPOINT ["cp", "/vmware-vix-disklib-distrib", "/opt/vmware-vix-disklib-distrib"]
 ---> Running in 65a8ff6a4c30
Removing intermediate container 65a8ff6a4c30
 ---> 76dd9c09d15e
Successfully built 76dd9c09d15e
Successfully tagged docker-registry-default.cloudapps.example.com/kubevirt/vddk:6.5.2-6195444

$ docker push $REGISTRY/kubevirt/vddk:$VERSION
The push refers to repository [docker-registry-default.cloudapps.example.com/kubevirt/vddk]
00b4867f2a15: Pushed
0e3b8915e127: Pushed
0b97b1c81a32: Pushed
6.5.2-6195444: digest: sha256:674dcb57fa0028986bb91afec8197c875b32ee8b29335a4552c1535ef2868bec size: 945
```

To use the content you have to add the conteiner as `initContainer` in the
conversion pod description:

```
  initContainers:
    - name: vddk-init
      image: 172.30.179.38:5000/kubevirt/vddk:6.5.2-6195444
      volumeMounts:
        - name: volume-vddk
          mountPath: /opt/vmware-vix-disklib-distrib
```

For the complete example how to run the conversion pod with the VDDK container
image see `examples/pod2.yml`.
