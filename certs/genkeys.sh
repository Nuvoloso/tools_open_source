#!/bin/bash
# Copyright 2019 Tad Lebeck
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Generate test server and client keys
# Insecure! only ca.key is encrypted, other *.key are not encrypted
# server cert is self-signed
#
# Requires input env CA_PASSWORD containing the password for the ca.key

# PATH update allows testing on MacOS, should have no effect on Linux
PATH=/usr/local/opt/openssl/bin:$PATH
export PATH
which openssl
# Create the CA Key and Certificate for signing Client Certs
# e.g:
# openssl genrsa -des3 -passout pass:"$CA_PASSWORD" -out ca.key 4096
# openssl req -new -x509 -days 3650 -key ca.key -passin pass:"$CA_PASSWORD" -set_serial 02 -out ca.crt -subj "/CN=nuvoloso.com/O=Nuvoloso/OU=Eng/C=US/ST=CA/L=Sunnyvale"
# The ca.key and ca.crt are pre-loaded, the password is a secret
if [ -z "$CA_PASSWORD" ]; then
    echo CA_PASSWORD should be set to the password of the ca.key 1>&2
    exit 1
fi
if [ ! -r ca.key ]; then
    echo ca.key file is missing 1>&2
    exit 1
fi
if [ ! -r ca.crt ]; then
    echo ca.crt file is missing 1>&2
    exit 1
fi

# create key, signed crt for a server (can also be used as a long-lived client, eg between backend services)
# extention(s) added if $3 is present
# parameters: filenameprefix DNprefix
createservercert() {
# Create the Server Key, CSR, and Certificate
    openssl genrsa -des3 -passout pass:server -out $1.key 2048
    openssl req -new -key $1.key -passin pass:server -out $1.csr -subj "$2/O=Nuvoloso/OU=Eng/C=US/ST=CA/L=Sunnyvale"

    # Sign the server certificate with our CA cert
    if [[ -n "$3" ]]; then
        openssl x509 -req -extfile <(printf "$3") -days 824 -in $1.csr -passin pass:"$CA_PASSWORD" -CA ca.crt -CAkey ca.key -set_serial 01 -out $1.crt
    else
        openssl x509 -req -days 824 -in $1.csr -passin pass:"$CA_PASSWORD" -CA ca.crt -CAkey ca.key -set_serial 01 -out $1.crt
    fi
    rm $1.csr
    # remove password requirement
    openssl rsa -in $1.key -passin pass:server -out newkey.pem && mv newkey.pem $1.key
}

# create key, signed crt and pem for a client
# parameters: filenameprefix DNprefix
createclientcert() {
    openssl genrsa -des3 -passout pass:client -out $1.key 1024
    openssl req -new -key $1.key -passin pass:client -out $1.csr -subj "$2/O=Nuvoloso/OU=Eng/C=US/ST=CA/L=Sunnyvale"

    # Sign the client certificate with our CA cert
    openssl x509 -req -days 365 -in $1.csr -passin pass:"$CA_PASSWORD" -CA ca.crt -CAkey ca.key -set_serial 01 -out $1.crt
    rm $1.csr
    # remove password requirement
    openssl rsa -in $1.key -passin pass:client -out newkey.pem && mv newkey.pem $1.key
    cat $1.crt $1.key > $1.pem
}

createservercert auth "/CN=auth.nuvoloso.com"
createservercert centrald "/CN=centrald.nuvoloso.com"
createservercert configdb "/CN=configdb.nuvoloso.com" # deprecated
createservercert configdbRS "/CN=configdb.nuvoloso.com" 'subjectAltName=DNS:configdb-0.configdb.nuvoloso-management.svc.cluster.local,DNS:configdb-1.configdb.nuvoloso-management.svc.cluster.local,DNS:configdb-2.configdb.nuvoloso-management.svc.cluster.local'
createservercert metricsdb "/CN=metricsdb.nuvoloso.com"
createservercert webservice "/CN=webservice.nuvoloso.com"
createservercert nginx "/CN=nginx.nuvoloso.com"

# create these as server certs: PEM not needed and want long expiry
createservercert nginxproxy "/CN=nginxproxy.nuvoloso.com"
createservercert gui "/CN=guiservice.nuvoloso.com"
createservercert mongosidecar "/CN=mongo-sidecar.nuvoloso.com"

createservercert agentd "/CN=agentd.nuvoloso.com"
createservercert clusterd "/CN=clusterd.nuvoloso.com"

createclientcert client "/CN=someone@nuvoloso.com"

for crt in *.crt; do
    if [ "$crt" = ca.crt ]; then
        continue
    fi
    openssl verify -verbose -CAfile ca.crt "$crt"
done
