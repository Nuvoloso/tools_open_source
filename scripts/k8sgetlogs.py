#! /usr/bin/env python2.7
"""
Copyright 2019 Tad Lebeck

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
"""
Usage: usage: k8sgetlogs.py podname [-n namespace] [-c container] [-p]

Downloads all of the available logs of one container from a kubernetes cluster
into a subdirectory with the path ./${podname}/${container}.  The subdirectory will
be created when the script is run.  Multiple containers from the same pod
will share the same parent ${podname} directory.

Dependencies:
- kubectl installed and in your path
- if the cluster is on AWS (kops or EKS):
  ssh configured with correct key to access the AWS instance
- if the cluster is a GKE cluster:
  1) gcloud (google-cloud-sdk) must be installed and in your path.
  2) you must have successfully used 'gcloud compute ssh' to log into the node
     at least once before running this script.
"""

import argparse
import json
import subprocess
import sys

# default login name for instance
DEFAULT_LOGIN = 'ubuntu'


def container_status(pod, args):
    """Find the status of the named container within the pod status.
    If no container is named in the args, its name will be set iff there is 1 container.
    """
    pod_name = pod['metadata']['name']
    statuses = pod['status']['containerStatuses']
    if 'initContainerStatuses' in pod['status']:
        statuses.extend(pod['status']['initContainerStatuses'])
    if not args.container:
        if len(statuses) > 1:
            print 'a container name must be specified for pod', pod_name
            sys.exit(1)
        status = statuses[0]
        args.container = status['name']
    else:
        for status in statuses:
            if status['name'] == args.container:
                return status
        print 'container %s is not valid for pod %s' % (args.container, pod_name)
        sys.exit(1)
    return status


def get_container_info(args):
    """use kubectl to determine the (private) hostname, pod UID and container type and ID.
    The values are added to the args as:
    private_hostname
    pod_uid
    c_id
    c_type (docker or containerd)
    c_restarts (number of restarts of the container)
    """
    cmd = [
        'kubectl',
        'get',
        'pod/%s' % args.pod,
        '-o', 'json',
    ]
    if args.context:
        cmd.append('--context=' + args.context)
    if args.namespace:
        cmd.extend(['-n', args.namespace])

    data = ''
    try:
        data = subprocess.check_output(cmd)
        pod = json.loads(data)
    except subprocess.CalledProcessError as exc:
        data = exc.output
        if data:
            print data
        sys.exit(1)
    except ValueError as exc:
        print '%s did not output valid JSON' % cmd
        print data
        sys.exit(1)

    status = container_status(pod, args)
    if args.previous:
        if 'lastState' in status and 'terminated' in status['lastState']:
            c_id = status['lastState']['terminated']['containerID']
        else:
            print 'container %s has no previous state' % status['name']
            sys.exit(1)
    else:
        c_id = status['containerID']

    args.private_name = pod['spec']['nodeName']
    args.pod_uid = pod['metadata']['uid']
    args.c_type, args.c_id = c_id.split('://', 1)
    args.restarts = 0
    if 'restartCount' in status:
        args.restarts = status['restartCount']


def get_public_host(args):
    """Given the private name of a kubernetes node (args.private_name), returns its public name.
    Also sets args.use_gcloud_ssh if GKE is detected and sets args.zone if label is present.
    """
    cmd = [
        'kubectl',
        'get',
        'node/%s' % args.private_name,
        '-o', 'json',
    ]
    if args.context:
        cmd.append('--context=' + args.context)

    data = ''
    try:
        data = subprocess.check_output(cmd)
        response = json.loads(data)
    except subprocess.CalledProcessError as exc:
        data = exc.output
        if data:
            print data
        sys.exit(1)
    except ValueError as exc:
        print '%s did not output valid JSON' % cmd
        print data
        sys.exit(1)

    args.use_gcloud_ssh = False
    labels = response['metadata']['labels']
    if 'failure-domain.beta.kubernetes.io/zone' in labels:
        args.zone = labels['failure-domain.beta.kubernetes.io/zone']
    if 'cloud.google.com/gke-nodepool' in labels:
        if not args.zone:
            print 'node is not labeled with failure-domain.beta.kubernetes.io/zone'
            sys.exit(1)
        args.use_gcloud_ssh = True
        # gcloud compute ssh expects the private name
        return args.private_name.encode('ascii')

    ext_ip = ''
    for res in response['status']['addresses']:
        if res['type'] == 'ExternalDNS':
            return res['address']
        if res['type'] == 'ExternalIP':
            ext_ip = res['address']
    if ext_ip:
        return ext_ip
    print 'cannot find instance with private name', args.private_name
    sys.exit(1)


def slurp_docker_logs(instance, args):
    """Downloads all of the available logs of one docker container from a kubernetes cluster
    into a subdirectory with the path ./${podname}/${container}.  The subdirectory will
    be created when the script is run.  Multiple containers from the same pod
    will share the same parent ${podname} directory.
    """

    c_id = args.c_id
    command = 'sudo sh -c "cp /var/lib/docker/containers/%s/%s-json.log* ." &&' \
        ' sudo sh -c "chown $USER %s-json.log* && chmod 600 %s-json.log*"' % (
            c_id, c_id, c_id, c_id)
    # the logs are readable only by root. Copy, chown and chmod them so they can be downloaded
    cmd = [
        'ssh',
        instance,
        '-l', args.login,
        '-o', 'StrictHostKeyChecking=no'
    ]
    if args.ssh_identity_file:
        cmd.extend(['-i', args.ssh_identity_file])
    if args.use_gcloud_ssh:
        # StrictHostKeyChecking=no, login and identity are automatically set by gcloud
        cmd = ['gcloud', 'compute', 'ssh', instance, '--zone=' + args.zone]
        cmd.extend(['--', '-oLogLevel=Error'])
    cmd.append(command)
    retcode = subprocess.call(cmd)
    if retcode:  # ssh already printed whatever error occurred
        sys.exit(1)

    dir_name = '%s/%s' % (args.pod, args.container)
    cmd = ['mkdir', '-p', dir_name]
    retcode = subprocess.call(cmd)
    if retcode:
        sys.exit(1)

    # scp prints status of the the copy, so no need for additional messages on success or failure
    cmd = ['scp', '-o', 'StrictHostKeyChecking=no']
    if args.ssh_identity_file:
        cmd.extend(['-i', args.ssh_identity_file])
    if args.use_gcloud_ssh:
        cmd = ['gcloud', 'compute', 'scp',
               '--scp-flag=-oLogLevel=Error', '--zone=' + args.zone]
    cmd.append('%s@%s:%s-json.log*' % (args.login, instance, c_id))
    cmd.append(dir_name)
    retcode = subprocess.call(cmd)
    if retcode:
        sys.exit(1)

    # delete the copy
    cmd = [
        'ssh',
        instance,
        '-l', args.login,
        '-o', 'StrictHostKeyChecking=no',
    ]
    if args.ssh_identity_file:
        cmd.extend(['-i', args.ssh_identity_file])
    if args.use_gcloud_ssh:
        cmd = ['gcloud', 'compute', 'ssh', instance, '--zone=' + args.zone]
        cmd.extend(['--', '-oLogLevel=Error'])
    cmd.append('rm %s-json.log*' % c_id)
    retcode = subprocess.call(cmd)
    if retcode:
        sys.exit(1)


def slurp_containerd_logs(instance, args):
    """Downloads all of the available logs of one containerd container from a kubernetes cluster
    into a subdirectory with the path ./${podname}/${container}.  The subdirectory will
    be created when the script is run.  Multiple containers from the same pod
    will share the same parent ${podname} directory.
    """

    latest = args.restarts
    if args.previous:
        latest -= 1
    namespace = 'default'
    if args.namespace:
        namespace = args.namespace
    path = '/var/log/pods/%s_%s_%s/%s/%d.log*' % (
        namespace, args.pod, args.pod_uid, args.container, latest)
    # the logs are readable only by root. Copy, chown and chmod them so they can be downloaded
    cmd = [
        'ssh',
        instance,
        '-l', args.login,
        '-o', 'StrictHostKeyChecking=no'
    ]
    if args.ssh_identity_file:
        cmd.extend(['-i', args.ssh_identity_file])
    if args.use_gcloud_ssh:
        # StrictHostKeyChecking=no, login and identity are automatically set by gcloud
        cmd = ['gcloud', 'compute', 'ssh', instance, '--zone=' + args.zone]
        cmd.extend(['--', '-oLogLevel=Error'])
    cmd.append('sudo sh -c "cp %s ." && sudo sh -c "chown $USER %d.log* && chmod 600 %d.log*"' % (
        path, latest, latest))
    retcode = subprocess.call(cmd)
    if retcode:  # ssh already printed whatever error occurred
        sys.exit(1)

    dir_name = '%s/%s' % (args.pod, args.container)
    cmd = ['mkdir', '-p', dir_name]
    retcode = subprocess.call(cmd)
    if retcode:
        sys.exit(1)

    # scp prints status of the the copy, so no need for additional messages on success or failure
    cmd = ['scp', '-o', 'StrictHostKeyChecking=no']
    if args.ssh_identity_file:
        cmd.extend(['-i', args.ssh_identity_file])
    if args.use_gcloud_ssh:
        cmd = ['gcloud', 'compute', 'scp',
               '--scp-flag=-oLogLevel=Error', '--zone=' + args.zone]
    cmd.append('%s:%d.log*' % (instance, latest))
    cmd.append(dir_name)
    retcode = subprocess.call(cmd)
    if retcode:
        sys.exit(1)

    # delete the copy
    cmd = [
        'ssh',
        instance,
        '-l', args.login,
        '-o', 'StrictHostKeyChecking=no',
    ]
    if args.ssh_identity_file:
        cmd.extend(['-i', args.ssh_identity_file])
    if args.use_gcloud_ssh:
        cmd = ['gcloud', 'compute', 'ssh', instance, '--zone=' + args.zone]
        cmd.extend(['--', '-oLogLevel=Error'])
    cmd.append('rm %d.log*' % latest)
    retcode = subprocess.call(cmd)
    if retcode:
        sys.exit(1)


def main():
    """main
    """

    # parse args
    parser = argparse.ArgumentParser(
        description="Kubernetes Kops container log retriever. " +
        "Requires SSH access to the node where the desired container is running")
    parser.add_argument('pod', help='The pod name')
    parser.add_argument('-i', '--ssh-identity-file',
                        help='File from which the SSH identity (private key) ' +
                        ' is read. Used to override default SSH behavior')
    parser.add_argument('-l', '--login', help='SSH login name. Default:' +
                        DEFAULT_LOGIN, default=DEFAULT_LOGIN)
    parser.add_argument(
        '--context', help='The name of the kubeconfig context to use')
    parser.add_argument('-c', '--container',
                        help='Get the logs of this container')
    parser.add_argument('-n', '--namespace',
                        help='The Kubernetes namespace')
    parser.add_argument('-p', '--previous', action='store_true',
                        help='Get the logs for the previous instance')

    args = parser.parse_args()
    get_container_info(args)  # adds more args, see the implementation
    public_host = get_public_host(args)
    if args.c_type == 'docker':
        slurp_docker_logs(public_host, args)
    else:  # assume containerd
        slurp_containerd_logs(public_host, args)


# launch the program
if __name__ == '__main__':
    main()
