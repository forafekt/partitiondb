#!/bin/sh
set -eu
umask 077
mkdir -p /tls /ca /bootstrap /runtime /static /media /bao-data
if [ ! -f /tls/server.key ]; then
  openssl req -x509 -nodes -newkey rsa:3072 -days 3650 \
    -subj '/CN=PartitionDB local infrastructure' \
    -addext 'subjectAltName=DNS:openbao,DNS:postgres-control,DNS:postgres-tenants,DNS:localhost,IP:127.0.0.1' \
    -keyout /tls/server.key -out /ca/ca.pem
fi
cp /ca/ca.pem /tls/server.crt
chmod 755 /tls /ca
chmod 644 /ca/ca.pem /tls/server.crt
chown 999:999 /tls/server.key
# PostgreSQL requires 0600 when its database user owns the private key.
chmod 600 /tls/server.key
# OpenBao receives its own key readable by the non-root application UID.
cp /tls/server.key /tls/bao.key
chown 10001:10001 /tls/bao.key
chmod 600 /tls/bao.key
chown 10001:10001 /runtime /static /media /bao-data
chmod 700 /runtime /bootstrap /bao-data
chmod 755 /static /media
