# scripts
Useful utility scripts.

## cluster_delete.py

Deletes a cluster object and other objects associated with the cluster (storage, CSP storage, nodes, pools, etc)
from the configuration database.
Volume-series are transitioned to UNBOUND and their snapshots are preserved by default,
but they can be deleted instead, eg to clean up a test cluster.
Note that deleting volume-series this way has no effect on the catalog or protection store.
This script is meant to be used after the corresponding kubernetes namespace has **ALREADY** been deleted.
Using the script while the kubernetes namespace is still present may lead to corruption, possibly requiring the entire installation to be deleted.

The script requires Internal Role access on nvcentrald. This can be either via direct access to its unix-domain socket (default) or remotely
by using a privileged certificate and private key pair.

The script will optionally mark active storage and volume series requests as FAILED and CANCELED respectively.
This is unsafe in general, because the requests could be active in nvcentrald (see the swagger API documentation), and changing the state
while nvcentrald is also changing the requests can lead to corruption. In addition, even if this option is
specified, additional requests created while the script is running can cause the script to fail.

See the script usage for more details.

## k8sgetlogs.py

Download container logs from a container in a kops or GKE (google) cluster.
Both docker and containerd are supported.

If the cluster is on GKE
* `gcloud` ([google-cloud-sdk](https://cloud.google.com/sdk/docs/downloads-interactive)) must be installed and in your path.
* You must have used `gcloud compute ssh` to log into the node at least once before running this script.

## k8slogview.py

Reads log files in the docker JSON log file format and outputs the messages the same way that `kubectl logs` does.
This is useful when the logs for a container have rolled over. In this case, `kubectl logs` only outputs the
most recent log file. But, you can download all of the available log files for a
container and use this script to decode and view them.

You can use `k8sgetlogs.py` (see above) to download the container logs. Otherwise...

To find and view all the log files for a container, follow this procedure:
- Find out the node where the container is or was executing and the container's docker container ID.
  Kubernetes always restarts containers on the same node, so you can use `kubectl describe pods -n ...` to find this information.
  Note that the "Node:" displayed is the private node name. You'll have to use the AWS console or CLI to determine the public name.
  Assume you want to get the logs for agentd running on some node, you would do this (partial output shown):
  ```
  kubectl describe pods -n nuvoloso-cluster | less
  ...
  Name:           nuvoloso-node-n4r8g
  Namespace:      nuvoloso-cluster
  Node:           ip-172-20-46-18.us-west-2.compute.internal/172.20.46.18
  Start Time:     Mon, 26 Nov 2018 09:37:10 -0800
  ...
  Containers:
    agentd:
      Container ID:  docker://5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25
      Image:         407798037446.dkr.ecr.us-west-2.amazonaws.com/nuvoloso/nvagentd:v1
  ...
  ```
  Find the **Node** and **Container ID** as shown in the example output above. In this case, the private node name
  is `ip-172-20-46-18.us-west-2.compute.internal` and the
  docker container ID is `5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25`.
  You can find the public node name or IP using the AWS EC2 console or the CLI `aws ec2 describe-instances | less`.

  If you need the logs for the previous instance an extra step is needed to get the full state of the desired pod,
  in this case `nuvoloso-node-n4r8g` found in the **Name** above:
  ```
  kubectl get pod/nuvoloso-node-n4r8g -n nuvoloso-cluster -o json | less
  ...
        "containerStatuses": [
            {
                "containerID": "docker://5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25",
                "image": "407798037446.dkr.ecr.us-west-2.amazonaws.com/nuvoloso/nvagentd:dlc",
                "imageID": "docker-pullable://407798037446.dkr.ecr.us-west-2.amazonaws.com/nuvoloso/nvagentd@sha256:83b7fd7795f9d374252819da726a2a8d2e38b1ec521daa1df863cd7012c39499",
                "lastState": {
                    "terminated": {
                        "containerID": "docker://731cb3e4cf535a424f1fd18a9cf0d31585783dc80e297f1369fffc864f84a768",
                        "exitCode": 137,
                        "finishedAt": "2018-11-28T16:21:15Z",
                        "reason": "Error",
                        "startedAt": "2018-11-27T17:05:30Z"
                    }
                },
                "name": "agentd",
                "ready": true,
                "restartCount": 2,
                "state": {
                    "running": {
                        "startedAt": "2018-11-28T16:21:22Z"
                    }
                }
            },
  ```
  In the json output, search for the `containerID` of the current instance (that "5cf..." value in this case).
  The Container ID for the previous instance is in the `terminated` sub-section.
  You could of course just inspect the JSON format to get all of the information at once, but it is easy to get lost in the lengthy output.
- Log into the node using the public node name or IP where the container is or was executing.
- `sudo bash` because you need to be root for the following steps, so be careful!
- Change to the container state directory. This directory is of the form `/var/lib/docker/containers/{containerID}`, so in this case for
  the current instance
  ```
  cd /var/lib/docker/containers/5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25
  ```
- List all of the log files available:
  ```
  ls *json.log*
  5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25-json.log
  5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25-json.log.1
  5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25-json.log.2
  5cfd537523d35005c3dcbe86efc939082956728ea893d93ccfb3f0b2d6466a25-json.log.3
  ```
  In this example, there are 4 log files, with the `log.3` file being the oldest. By default, Kops configures
  docker to keep up to 5 log files of 10MB each.
- For the most complete log, collect all of the `*json.log*` files.
- Finally, to view the logs in FORWARD chronological order:
  `ls -r *json.log* | xargs k8slogview.py | less`

## nvmon.sh
This script provides a terminal based monitor of system activity, including
- clusters and nodes
- storage pools
- storage objects
- volume objects
- recent storage requests
- recent volume series requests

It requires **nvctl** to be on the search path, and configured
to communicate with the Nuvoloso system of interest,
typically by creating and initializing the `$HOME/.nuvoloso/nvctl.config` file.

The script watches for request object changes to avoid polling the system,
however it will periodically update itself to pick up changes to other objects.

Use a very large window when running this script as the tables it displays can be very wide.

## agentdlog2cmd.py
This tool processes an agentd.log file and produces a runnable script to reproduce the nuvo API calls.
This tool should be able to process both agentd.log and agentd-json.log files
If you have multiple split agentd.log files they should be concatenated together before running the tool, otherwise the output may be incomplete.
The output script should be runnable, however it's more likely than not that hand editing will be necessary.
There are inline comments added to the script output when a failed command was encountered.
A new run#() function is created each time a new startup of the nuvo process is detected.
By default the script will execute only the run0() function.
See the command usage for more details.

