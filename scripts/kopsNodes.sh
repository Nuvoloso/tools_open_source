#!/bin/sh
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

aws ec2 describe-instances | jq 'def TagName: .[] | select(.Key == "Name")|.Value;
def TagCluster: .[] | select(.Key == "KubernetesCluster")|.Value;
def TagNode: .[] | select(.Key == "k8s.io/role/node")|.Value;
def TagMaster: .[] | select(.Key == "k8s.io/role/master")|.Value;
.Reservations[].Instances[] |
 select(.State.Name == "running") |
 select(.Tags[].Key == "KubernetesCluster") |
 { state: .State.Name,
   ready: .SourceDestCheck | not,
   pvtDNS: .PrivateDnsName,
   pvtIP: .PrivateIpAddress,
   pubDNS: .PublicDnsName,
   pubIP: .PublicIpAddress,
   id: .InstanceId,
   name: .Tags|TagName,
   cluster: .Tags|TagCluster
 }'
