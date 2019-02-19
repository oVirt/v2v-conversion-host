FROM alpine:3.8

ENV OPERATOR=/usr/local/bin/v2v-vmware \
    USER_UID=1001 \
    USER_NAME=v2v-vmware

# install operator binary
COPY build/_output/bin/v2v-vmware ${OPERATOR}

COPY build/bin /usr/local/bin
RUN  /usr/local/bin/user_setup

ENTRYPOINT ["/usr/local/bin/entrypoint"]

USER ${USER_UID}
