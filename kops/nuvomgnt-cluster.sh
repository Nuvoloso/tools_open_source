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

set -x

NAME=nuvomgmt.k8s.local

kops create cluster \
 --node-count=1 \
 --node-volume-size 10 \
 --node-size=t2.medium \
 --master-size=t2.medium \
 --master-volume-size 20 \
 --zones us-west-2a \
 --image=ami-6e1a0117 \
 --ssh-public-key "~/.ssh/id_rsa.pub" \
 --kubernetes-version=${K8SVER:-1.7.8} \
 ${NAME}

kops update cluster $NAME --yes
