#!/usr/bin/env bash

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

NUM_NODES=$(kubectl get nodes| wc -l)
((NUM_NODES -= 1))
echo "Number of nodes $NUM_NODES"
MASTERS=$((NUM_NODES / 2 + 1))
echo "Minimum number of master eligible nodes $MASTERS"
sed -e "s/{{numnodes}}/$NUM_NODES/" -e "s/{{nummasters}}/$MASTERS/" EFK_stack.yaml.tmpl  | kubectl apply -f -
kubectl rollout status sts/es-cluster --namespace=logging
KIBANA=$(kubectl get pods -o go-template --template '{{range .items}}{{.metadata.name}}{{"\n"}}{{end}}' -n logging | grep "kibana")
echo "visit http://localhost:5601/app/kibana" 
echo "kubectl port-forward $KIBANA 5601:5601 -n logging"
kubectl port-forward $KIBANA 5601:5601 -n logging
