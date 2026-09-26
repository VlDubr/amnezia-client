# A stand-in for a VPN server: sshd + sudo + docker CLI talking to the host's Docker daemon.
FROM alpine:3.20
RUN apk add --no-cache openssh sudo docker-cli bash \
    && ssh-keygen -A \
    && adduser -D -s /bin/bash panel \
    && echo "panel:panel-test-password" | chpasswd \
    && echo "panel ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/panel \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && echo "AllowTcpForwarding no" >> /etc/ssh/sshd_config
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
