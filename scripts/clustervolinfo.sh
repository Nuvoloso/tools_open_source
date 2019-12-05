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

SSH_OPTIONS="-oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null"

get_nuvo_node_list() {
    local node_list=()

    node_list=( $(nvctl node list -o json | jq -r '.[].meta.id') )
    #if [[ $? -ne 0 || ${#node_list[@]} -eq 0 ]] ; then
    #    echo "No nodes found." >&2
    #fi
    echo "${node_list[@]}"
}

get_nuvo_node_publichostname() {
    echo $(nvctl node list -o json | jq -r ".[] | select(.meta.id == \"$1\") | .nodeAttributes.\"public-hostname\".value")
}

get_nuvo_node_localip() {
    echo $(nvctl node list -o json | jq -r ".[] | select(.meta.id == \"$1\") | .nodeAttributes.LocalIP.value")
}

get_nuvo_device_list() {
    local node="$1"
    local device_list=()

    if [[ -z "${node}" ]] ; then
        device_list=( $(nvctl storage list -o json | jq -r '.[].meta.id') )
    else
        device_list=( $(nvctl storage list -o json | jq -r ".[] | select(.storageState.attachedNodeId == \"${node}\") | .meta.id") )
    fi
    if [[ $? -ne 0 || ${#device_list[@]} -eq 0 ]] ; then
        echo "No devices found." >&2
        device_list=()
    fi
    echo "${device_list[@]}"
}

get_nuvo_device_devnode() {
    echo $(nvctl storage list -o json | jq -r ".[] | select(.meta.id == \"$1\") | .storageState.attachedNodeDevice")
}

get_nuvo_device_type() {
    echo $(nvctl storage list -o json | jq -r ".[] | select(.meta.id == \"$1\") | .cspStorageType")
}

get_nuvo_device_size() {
    echo $(nvctl storage list -o json | jq -r ".[] | select(.meta.id == \"$1\") | .sizeBytes")
}

get_nuvo_vs_list() {
    local node="$1"
    local vs_list=()

    if [[ -z "${node}" ]] ; then
        vs_list=( $(nvctl vs list -o json | jq -r '.[].meta.id') )
    else
        vs_list=( $(nvctl vs list -o json | jq -r ".[] | select(.mounts[].mountedNodeId == \"${node}\") | .meta.id") )
    fi
    if [[ $? -ne 0 || ${#vs_list[@]} -eq 0 ]] ; then
        echo "No volume series found." >&2
        vs_list=()
    fi
    echo "${vs_list[@]}"
}

get_nuvo_vs_fusedevice() {
    echo $(nvctl vs list -o json | jq -r ".[] | select(.meta.id == \"$1\") | .mounts[].mountedNodeDevice")
}

get_nuvo_containers() {
    echo $(kubectl get pods -o json -n nuvoloso-cluster | jq -r ".items[] | select(.status.hostIP == \"$1\" and .status.containerStatuses[].name == \"nuvo\") | .metadata.name")
}


get_nuvo_info() {
    local node_list=()

    node_list=( $(get_nuvo_node_list) )
    if [[ ${#node_list[@]} -eq 0 ]]; then
        echo "No nodes found"
    else
        for node in ${node_list[@]} ; do
            publichostname=$(get_nuvo_node_publichostname ${node})
            echo ${publichostname}
            echo "Backend Storage:"
            device_list=( $(get_nuvo_device_list ${node}) )
            for device in ${device_list[@]} ; do
                echo "$(get_nuvo_device_type ${device}): $(get_nuvo_device_devnode ${device}): $(get_nuvo_device_size ${device}) ${device}"
            done
            echo "Nuvo Volumes:"
            vs_list=( $(get_nuvo_vs_list ${node}) )
            for vs in ${vs_list[@]} ; do
                volfile="/var/local/nuvoloso/$(get_nuvo_vs_fusedevice ${vs})"
                info=$(ssh ubuntu@${publichostname} "sudo ls -lh ${volfile}")
                echo ${info}
            done
            echo "Nuvo Container Pod"
            localip=$(get_nuvo_node_localip ${node})
            echo $(get_nuvo_containers ${localip})
            echo "Nuvo Process"
            nuvops=$(ssh ${SSH_OPTIONS} ubuntu@${publichostname} "ps -C nuvo -o lstart,etime,pid,ppid,pcpu,pmem,vsz,args")
            if [[ -z "${nuvops}" ]] ; then
                echo "No nuvo process is running"
            else
                echo "${nuvops}"
            fi
            echo
        done
    fi
}


get_nuvo_info
