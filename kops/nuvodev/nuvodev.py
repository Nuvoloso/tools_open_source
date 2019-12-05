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
Wrapper script for the 'kops' command to be used in Nuvoloso development
Copyright Nuvoloso, 2019

Dependencies
- Create virtualenv
- pip install boto3
- pip install 'ruamel.yaml<=0.15'
- aws installed with credentials

Overall flow
- Check for kops/aws installation
- Check/prompt for SDK AWS credentials
- Create kops user with correct permissions, capturing output to pull
  out the access key and id
- Set environment variables for profile configuration
  Check to see if a KOPS_STATE_STORE was provided, attempt to create it,
  or default to a well known name
- Default to us-west-2/us-west-2a region/zone
- Execute kops cluster creation
- Launch the cluster
- Wait for the cluster to be ready (kops validate cluster or similar)
- open port range 30000-32767 on nodes security group to current IP
- Install the dashboard
- Install Heapster

**** NOT DONE ****
TBD if we want to launch dev env in AWS after
(Build our software on a slave with our dev container)
Deploy nuvodeployment.yaml
Wait for it to be ready
"""

import argparse
import ConfigParser
import httplib
import subprocess
import tempfile
import datetime
import os
from os.path import expanduser
import time
import sys
import re
from distutils.version import StrictVersion  # pylint: disable=no-name-in-module,import-error; Due to pylint error-https://github.com/PyCQA/pylint/issues/73
import boto3
from botocore.exceptions import ClientError
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import PreservedScalarString

# global debug variable
DEBUG = False

# default kops username and group
KOPS_USER = 'kops'
KOPS_GROUP = 'kops'

# kubernetes repo
KUBERNETES_REPO = 'https://raw.githubusercontent.com/kubernetes/'

# default AMI to use for all instances
# Note that when using ubuntu 18.04, all instance groups must use ubuntu 18.04
DEFAULT_AMI = '099720109477/ubuntu/images/hvm-ssd/ubuntu-bionic-18.04-amd64-server-20190918'

# default node size used in kops
DEFAULT_NODE_SIZE = 't3.medium'

# default region for clusters
DEFAULT_REGION = 'us-west-2'

# default bucket name for store
DEFAULT_KOPS_BUCKET = 'kops-nuvoloso'

# full path to store
DEFAULT_KOPS_STATE_STORE = 's3://' + DEFAULT_KOPS_BUCKET

# default cluster name used in kops
DEFAULT_CLUSTER_NAME = 'nuvotest.k8s.local'

# delay length while we wait for user to be created in AWS
AWS_USER_CREATE_DELAY = 10

# default k8s version
DEFAULT_K8S_VERSION = '1.14.6'

# default k8s version when using CSI
DEFAULT_K8S_VERSION_FOR_CSI = '1.14.6'

# default kops version, and minimum supported
DEFAULT_KOPS_VERSION = '1.11.1'


def which(name):
    """ equivalent of the which command
    """
    try:
        devnull = open(os.devnull)
        subprocess.Popen([name], stdout=devnull,
                         stderr=devnull).communicate()
    except OSError as exc:
        if exc.errno == os.errno.ENOENT:
            return False
    return True


def validate_dependencies():
    """check dependencies are in place
    """
    aws_installed = which("aws")
    kops_installed = which("kops")

    if aws_installed and kops_installed:
        print "Dependencies are installed"
    else:
        raise Exception("Make sure AWS and kops are installed")


def validate_kops_version():
    """check to see if valid kops version is being used
    """
    cmd_list = [
        'kops',
        'version'
    ]
    print cmd_list
    data = ''
    try:
        data = subprocess.check_output(cmd_list)
    except subprocess.CalledProcessError as exc:
        data = exc.output
        if data:
            print data
        return False
    version = re.search(r'Version\s*([\d.]+)', data).group(1)
    if StrictVersion(version) < StrictVersion(DEFAULT_KOPS_VERSION):
        return False
    return True


def check_sdk_credentials():
    """ We need to start out with these credentials specified for
    access by the SDK.  A separate set will be used later
    and created in the credentials file for the kops user.

    """
    aws_secret_access_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
    aws_access_key_id = os.environ.get('AWS_ACCESS_KEY_ID')

    if DEBUG:
        print "AWS KEY: ", aws_secret_access_key
        print "AWS ID: ", aws_access_key_id
    return aws_access_key_id != None and aws_secret_access_key != None


def create_kops_user_group(user_name, group_name):
    """creates the user and adds them to the kops group
    """
    try:
        iam = boto3.client('iam')

        print "Creating group", group_name
        iam.create_group(
            GroupName=group_name,
        )
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'EntityAlreadyExists':
            print "Group", group_name, "already exists, continuing"
        else:
            print "Unexpected error: %s" % exc
            return

    try:
        print "Attaching policies"
        iam.attach_group_policy(
            PolicyArn="arn:aws:iam::aws:policy/AmazonEC2FullAccess",
            GroupName=group_name
        )
        iam.attach_group_policy(
            PolicyArn="arn:aws:iam::aws:policy/AmazonRoute53FullAccess",
            GroupName=group_name
        )
        iam.attach_group_policy(
            PolicyArn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
            GroupName=group_name
        )
        iam.attach_group_policy(
            PolicyArn="arn:aws:iam::aws:policy/IAMFullAccess",
            GroupName=group_name
        )
        iam.attach_group_policy(
            PolicyArn="arn:aws:iam::aws:policy/AmazonVPCFullAccess",
            GroupName=group_name
        )
    except ClientError as exc:
        print "Unexpected error: %s" % exc
        return

    try:
        print "Creating user", user_name
        iam.create_user(
            UserName=user_name
        )
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'EntityAlreadyExists':
            print "User already exists, continuing"
        else:
            print "Unexpected error: %s" % exc
            return

    try:
        print "Adding user to group"
        iam.add_user_to_group(
            GroupName=group_name,
            UserName=user_name
        )
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'EntityAlreadyExists':
            print "user already exists"
        else:
            print "Unexpected error: %s" % exc


def get_creds_from_file(user_name):
    """Get credentials from file
    """
    key_id = None
    key_value = None
    path = expanduser("~") + "/.aws/credentials"

    parser = ConfigParser.ConfigParser()

    try:
        parser.read(path)

        key_id = parser.get(user_name, "AWS_ACCESS_KEY_ID")
        key_value = parser.get(user_name, "AWS_SECRET_ACCESS_KEY")
    except ConfigParser.NoSectionError:
        print "Cannot find entries for user:", user_name
        return None
    except ConfigParser.Error as exc:
        print "Unexpected error: %s" % exc
        return None

    try:
        sys.stdout.write("Validating in AWS...")
        iam = boto3.client('iam')

        # See if user already exists
        iam.get_user(
            UserName=user_name
        )
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'NoSuchEntity':
            print "...user not found.  Credentials file are out of date."
            print "Remove the", user_name, "profile from the credentials file"
            sys.exit(1)
        else:
            print "...failed"
            print "Unexpected error: %s" % exc
            sys.exit(1)

    print "ok"
    print "AWS_ACCESS_KEY_ID =", key_id
    print "AWS_SECRET_ACCESS_KEY =", key_value
    # mimic the dict returned by the AWS SDK when creating credentials
    keyinfo = {}
    keyinfo['AccessKey'] = {}
    keyinfo['AccessKey']['AccessKeyId'] = key_id
    keyinfo['AccessKey']['SecretAccessKey'] = key_value
    return keyinfo


def create_and_store_key_info(user_name):
    """ Create and store key info """

    try:
        print "Create and store access key for", user_name
        iam = boto3.client('iam')

        # See if user already exists
        iam.get_user(UserName=user_name)
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'NoSuchEntity':
            print "User not found, creating profile"
        else:
            print "Unexpected error: %s" % exc
            sys.exit(1)

    # Attempt to create and store the key
    try:
        keyinfo = iam.create_access_key(UserName=user_name)
        key_id = keyinfo['AccessKey']['AccessKeyId']
        key_secret = keyinfo['AccessKey']['SecretAccessKey']
        if DEBUG:
            print keyinfo

        store_profile(user_name, key_id, key_secret)
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'LimitExceeded':
            print "Too many credentials exist for this user (it is limited to 2)."
            print "Each time this script is run it will create credentials for the specified user"
            print "Clean up the existing groups/users and re-run the script."
            sys.exit(1)
        else:
            print "Unexpected error: %s" % exc
            return None

    return keyinfo


def kops_state_store(client, args):
    """create the bucket for the kops state store
    """

    try:
        if args.region != 'us-east-1':
            res = client.create_bucket(
                ACL='private',
                Bucket=args.kops_bucket,
                CreateBucketConfiguration={
                    'LocationConstraint': args.region
                },
            )
        else:
            res = client.create_bucket(
                ACL='private',
                Bucket=args.kops_bucket,
            )
        print "bucket created at", res['Location']
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'BucketAlreadyOwnedByYou':
            print "Bucket " + args.kops_bucket + " already exists and is owned by user, continuing"
            return
        elif exc.response['Error']['Code'] == 'BucketAlreadyExists':
            print "Bucket " + args.kops_bucket + " already exists and is owned by " + \
                "someone else, this will probably fail, but continuing"
        elif exc.response['Error']['Code'] == 'IllegalLocationConstraintException' and \
                args.region == 'us-east-1':
            print "Bucket " + args.kops_bucket + " already exists in another region, continuing"
        else:
            print "Unexpected error: %s" % exc


def store_profile(user_name, key_id, key_secret):
    """store the given key id/secret at the end of the standard AWS credentials file
    """
    home = expanduser("~")
    path = home + "/.aws/credentials"
    with open(path, "a") as myfile:
        myfile.write("[" + user_name + "]" + "\n")
        myfile.write("AWS_ACCESS_KEY_ID=" + key_id + "\n")
        myfile.write("AWS_SECRET_ACCESS_KEY=" + key_secret + "\n")


def check_for_ubuntu_bionic(args):
    """Ubuntu bionic 18.04 requires some specific settings in the cluster config.
    Check the AMI requested in the args to see if it looks like a bionic AMI and set
    args.bionic = True if so. See edit_kops_cluster() for how this is used.

    The args.image can be specified either as "ami-*" or location, "owner/path".
    If the former, look up the AMI to find its location. Then check the location
    to see if it is "ubuntu-bionic".
    """
    image = args.image
    if image.startswith('ami-'):
        try:
            client = boto3.client('ec2', region_name=args.region)
            response = client.describe_images(
                ImageIds=[image]
            )
            if 'Images' not in response or len(response['Images']) != 1 or \
                    'ImageLocation' not in response['Images'][0]:
                print 'Unexpected response for describe_images:', response
                return 1
            image = response['Images'][0]['ImageLocation']
        except ClientError as exc:
            print 'Unexpected error: %s' % exc
            return 1

    if '-arm64-' in image:
        print 'Wrong architecture:', image
        print 'Only amd64 images can be used with kops'
        return 1

    args.bionic = False
    if '/ubuntu-bionic-18.04' in image:
        print 'Ubuntu Bionic 18.04 detected'
        print 'IMPORTANT: All instance groups must use Ubuntu Bionic 18.04!'
        args.bionic = True

    return 0


def launch_kops_create(args, state_store):
    """Launch the command to create the kops cluster"""
    ssh_path = expanduser("~") + "/.ssh/id_rsa.pub"
    cmd_list = [
        'kops',
        'create',
        'cluster',
        '--node-count=%d' % args.nodes,
        '--node-volume-size=10',
        '--node-size=%s' % args.node_size,
        '--master-size=t3.medium',
        '--master-volume-size=20',
        '--zones=%s' % args.zone,
        '--image=%s' % args.image,
        '--state=%s' % state_store,
        '--ssh-public-key=%s' % ssh_path,
        '--kubernetes-version=%s' % args.kube_version,
        '--authorization=RBAC',
        '--name=%s' % args.cluster_name
    ]

    print cmd_list
    retcode = subprocess.call(cmd_list)
    print "kops execution code", retcode

    return retcode


def edit_cluster(cluster_name, state_store, args):
    """Edit the cluster configuration to enable MountPropagation. MountPropagation
    is needed by the nuvo container so its FUSE mount head will be visible on
    the host.
    """
    cmd_list = [
        'kops', 'get', 'cluster', '--name=%s' % cluster_name, '--state=%s' % state_store,
        '-o', 'yaml'
    ]

    print cmd_list

    retcode = 0
    data = ''
    try:
        data = subprocess.check_output(cmd_list)
    except subprocess.CalledProcessError as exc:
        retcode = exc.returncode
        data = exc.output
        if data:
            print data

    print 'kops execution code', retcode
    if retcode != 0:
        return retcode

    yaml = YAML()
    try:
        cluster = yaml.load(data)
        if 'spec' not in cluster:
            raise ValueError
    except ValueError as exc:
        print '%s did not output valid yaml' % cmd_list
        print data
        return 1

    cluster['spec']['kubeAPIServer'] = {
        'allowPrivileged': True,
    }
    cluster['spec']['kubelet'] = {
        'allowPrivileged': True,
        'anonymousAuth': False,
        'featureGates': {
            'ExperimentalCriticalPodAnnotation': 'true'
        }
    }

    print "Container log max size:  " + str(args.container_log_max_size) + "m"
    print "Container log max files: " + str(args.container_log_max_files)
    cluster['spec']['docker'] = {
        'logOpt': [
            "max-size=" + str(args.container_log_max_size) + "m",
            "max-file=" + str(args.container_log_max_files)
        ]
    }
    if args.bionic:
        # resolvConf is required to work around the bug that kubernetes does not pick up
        # the correct resolv.conf when systemd-resolved is used.
        # See https://github.com/kubernetes/kubeadm/issues/273
        # While this is fixed in kubeadm, kops does not use kubeadm.
        # Kops does not have a suitable built-in solution for this problem.
        cluster['spec']['kubelet']['resolvConf'] = '/run/systemd/resolve/resolv.conf'

    data = yaml.dump(cluster)
    tmpname = ''
    with tempfile.NamedTemporaryFile(prefix='cluster', suffix='.yaml', delete=False) as myfile:
        tmpname = myfile.name
        myfile.write(data)

    cmd_list = ['kops', 'replace', '-f', tmpname, '--state=%s' % state_store]
    print cmd_list

    retcode = subprocess.call(cmd_list)
    print 'kops execution code', retcode

    if retcode == 0:
        os.remove(tmpname)
    else:
        print 'preserved temp file', tmpname
    return retcode


def edit_instance_group(cluster_name, state_store):
    """Edit instance group configuration to disable default mount point creation on ephemeral0,
    and add nvme-cli package, just in case.
    """
    cmd_list = [
        'kops', 'get', 'ig', 'nodes', '--name=%s' % cluster_name, '--state=%s' % state_store,
        '-o', 'yaml'
    ]
    print cmd_list

    retcode, data = 0, ''
    try:
        data = subprocess.check_output(cmd_list)
    except subprocess.CalledProcessError as exc:
        retcode = exc.returncode
        data = exc.output
        if data:
            print data

    print 'kops execution code', retcode
    if retcode != 0:
        return retcode

    yaml = YAML()
    try:
        inst_group = yaml.load(data)
        if 'spec' not in inst_group:
            raise ValueError
    except ValueError as exc:
        print '%s did not output valid yaml' % cmd_list
        print data
        return 1

    inst_group['spec']['additionalUserData'] = [{
        'name': 'nuvo_customization.txt',
        'type': 'text/cloud-config',
        'content': PreservedScalarString(
            '#cloud-config\nmounts:\n- [ ephemeral0 ]\npackages:\n- nvme-cli\n')
    }]

    data = yaml.dump(inst_group)
    tmpname = ''
    with tempfile.NamedTemporaryFile(prefix='inst_group', suffix='.yaml', delete=False) as myfile:
        tmpname = myfile.name
        myfile.write(data)

    cmd_list = ['kops', 'replace', '-f', tmpname, '--state=%s' % state_store]
    print cmd_list

    retcode = subprocess.call(cmd_list)
    print 'kops execution code', retcode

    if retcode == 0:
        os.remove(tmpname)
    else:
        print 'preserved temp file', tmpname
    return retcode


def launch_kops_update(cluster_name, state_store, yesoption):
    """ Launch kops update
    yesoption will turn on '--yes' to trigger actual creation of resources
    """
    cmd_list = [
        'kops',
        'update',
        'cluster',
        '--name=%s' % cluster_name,
        '--state=%s' % state_store
    ]
    if yesoption:
        cmd_list.append('--yes')

    print cmd_list
    retcode = subprocess.call(cmd_list)
    print "kops execution code", retcode
    return retcode


def wait_for_cluster(cluster_name, state_store):
    """Wait for the cluster to be created by running 'kops validate cluster'
    and checking return codes
    """
    cmd_list = [
        'kops',
        'validate',
        'cluster',
        '--name=%s' % cluster_name,
        '--state=%s' % state_store
    ]

    count = 0
    while True:
        retcode = subprocess.call(cmd_list)
        if retcode == 0:
            break
        count = count + 1
        if count == 15:  # was 10 but sometimes it takes longer in us-east-1
            return 1
        print "Waiting @", str(datetime.datetime.now()), "count is ", count, "/ 15"
        print "sleeping for 1 minute"
        time.sleep(60)

    return 0


def update_nodes_security_group(cluster_name, args):
    """
    Expose the 30000-32767 port range in the AWS nodes security group to the current IP
    so NodePort services (e.g. the nginx service in the nuvo deployment) can be accessed.
    """
    check_ip = 'checkip.amazonaws.com'
    try:
        # AWS provides a service to get the current client public IP
        conn = httplib.HTTPSConnection(check_ip)
        conn.request('GET', '/')
        resp = conn.getresponse()
        if resp.status != httplib.OK:
            print 'Failure: https://%s GET /:' % check_ip, resp.status, resp.reason
            return
        my_ip = resp.read().strip()
        if not my_ip:
            print 'Unexpected empty response from https://%s' % check_ip
            return
    except httplib.HTTPException as exc:
        print 'Unexpected error from https://%s: %s' % (check_ip, exc)
        return

    try:
        # get groupId of the nodes security group created by kops for this cluster
        client = boto3.client('ec2', region_name=args.region)
        response = client.describe_security_groups(
            Filters=[
                {
                    'Name': 'group-name',
                    'Values': ['nodes.%s' % cluster_name]
                }
            ]
        )
        if 'SecurityGroups' not in response or len(response['SecurityGroups']) != 1:
            print 'Unexpected response for describe_security_groups:', response
            return
        group = response['SecurityGroups'][0]
        group_id = group['GroupId']
        print 'Adding ingress rule for TCP ports 30000-32727 from current IP', my_ip, \
            'to security group', group_id
        client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    'IpProtocol': 'tcp',
                    'IpRanges': [{'CidrIp': '%s/32' % my_ip}],
                    'FromPort': 30000,
                    'ToPort': 32767
                }
            ]
        )
    except ClientError as exc:
        print 'Unexpected error: %s' % exc


def deploy_dashboard():
    """Deploy the K8S dashboard via kubectl
    """
    cmd_list = [
        'kubectl',
        'apply',
        '-f',
        KUBERNETES_REPO + 'dashboard/v1.10.0/src/deploy/recommended/kubernetes-dashboard.yaml'
    ]
    retcode = subprocess.call(cmd_list)
    if retcode != 0:
        print "Deploy dashboard failed, manually retry using:", ' '.join(cmd_list)
    return retcode


def deploy_heapster():
    """Deploy heapster for metrics via kubectl
    """
    cmd_list = [
        'kubectl',
        'apply',
        '-f',
        KUBERNETES_REPO + 'kops/master/addons/monitoring-standalone/v1.7.0.yaml'
    ]
    return subprocess.call(cmd_list)


def enable_auth_dashboard():
    """
    Enable dashboard by giving it cluster admin rights
    """
    authorize_dashboard = [
        'kubectl',
        'create',
        'clusterrolebinding',
        'kubernetes-dashboard',
        '-n',
        'kube-system',
        '--clusterrole=cluster-admin',
        '--serviceaccount=kube-system:kubernetes-dashboard'
    ]
    retcode = subprocess.call(authorize_dashboard)
    if retcode != 0:
        print "Error authorizing dashboard"


def summary_message(cluster_name, user_name, state_store):
    """Display summary message with important environment variables listed
    """
    print
    print "====================================================================================="
    print "Cluster name:", cluster_name
    print "AWS_ACCESS_KEY_ID={0}".format(os.environ['AWS_ACCESS_KEY_ID'])
    print "AWS_SECRET_ACCESS_KEY={0}".format(os.environ['AWS_SECRET_ACCESS_KEY'])
    print "KOPS_STATE_STORE={0}".format(state_store)
    print
    print "Note that the credentials are in your AWS credentials file"
    print "in $HOME/.aws/credentials as user", user_name
    print "You can change your default profile to reference those credentials by making"
    print "it your 'default' profile, or be sure to set the credential environment variables"
    print "when running this script or 'kops'."
    print
    print "To access the kubernetes dashboard run"
    print "   kubectl proxy"
    print "While the proxy is running, browse to"
    print "   http://localhost:8001/api/v1/namespaces/kube-system/services/" + \
        "https:kubernetes-dashboard:/proxy/"
    print "Skip the authentication token prompt for now."


def execute(args, state_store, user_name):
    """
    After all the prechecks are completed, actually create the cluster
    """
    print "===> Checking AMI"
    if check_for_ubuntu_bionic(args) != 0:
        # check_for_ubuntu_bionic prints its own error message
        sys.exit(1)

    print "===> Creating cluster"
    if launch_kops_create(args, state_store) != 0:
        print "kops failed to create the cluster, probably because it exists already."
        print "Delete the old cluster with"
        print "# kops delete cluster", '--name=%s' % args.cluster_name, "--yes"
        print "and retry the script."
        sys.exit(1)

    print '===> Editing cluster to add MountPropagation and other customization'
    if edit_cluster(args.cluster_name, state_store, args) != 0:
        print 'kops get or kops replace failed, unable to continue'
        sys.exit(1)

    print '===> Editing nodes instance group to customize cloud-init mounts and packages'
    if edit_instance_group(args.cluster_name, state_store) != 0:
        print 'kops get or kops replace failed, unable to continue'
        sys.exit(1)

    print "===> Validating cluster before launch"
    if launch_kops_update(args.cluster_name, state_store, False) != 0:
        print "kops update to validate cluster configuration failed"
        sys.exit(1)

    print "===> Launching cluster"
    if launch_kops_update(args.cluster_name, state_store, True) != 0:
        print "kops update to launch the cluster failed"
        sys.exit(1)

    print "===> Waiting for cluster"
    if wait_for_cluster(args.cluster_name, state_store) != 0:
        print "Cluster never became ready, exiting. You can check cluster readiness with:"
        print "# kops validate cluster", '--name=%s' % args.cluster_name
        sys.exit(1)

    print "===> Updating security group of the nodes"
    update_nodes_security_group(args.cluster_name, args)

    # install dashboard, do not wait for it
    print "===> Deploying dashboard"
    deploy_dashboard()
    enable_auth_dashboard()

    # install heapster, do not wait for it
    print "===> Deploying heapster"
    deploy_heapster()

    summary_message(args.cluster_name, user_name, state_store)


def update_network_security_group(user_name, cluster_name, args):
    """Get credentials and update just the security group
    """
    get_creds_from_file(user_name)
    print "===> Updating security group of the nodes"
    update_nodes_security_group(cluster_name, args)


def print_start_summary(args, user_name, group_name, state_store):
    """Display summary of starting environment
    """
    print "Starting operations with these parameters:"
    print "Cluster: ", args.cluster_name
    print "User:", user_name
    print "Group:", group_name
    print "State store:", state_store
    print "Region:", args.region
    print "Zone:", args.zone
    print "Node type:", args.node_size
    print "Node count:", args.nodes
    print "Image:", args.image
    print "Update network only:", args.update_network_sg


def make_parser_args():
    """Create parser arguments"""
    if os.environ.get('KOPS_CLUSTER_NAME') is None:
        os.environ['KOPS_CLUSTER_NAME'] = DEFAULT_CLUSTER_NAME
    default_cluster_name = os.environ.get('KOPS_CLUSTER_NAME')

    if os.environ.get('KOPS_STATE_BUCKET') is None:
        os.environ['KOPS_STATE_BUCKET'] = DEFAULT_KOPS_BUCKET
    kops_bucket = os.environ.get('KOPS_STATE_BUCKET')

    if os.environ.get('KOPS_STATE_STORE') is None:
        os.environ['KOPS_STATE_STORE'] = "s3://" + kops_bucket
    state_store = os.environ.get('KOPS_STATE_STORE')
    # prefer kops bucket derived from KOPS_STATE_STORE
    kops_bucket = state_store.split('//')[1]

    parser = argparse.ArgumentParser(description="Wrapper for 'kops' commands",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--cluster-name', help='name of kops cluster', default=default_cluster_name)
    parser.add_argument(
        '--kops-bucket', help='state store for cluster', default=kops_bucket)
    parser.add_argument(
        '--kube-version', help='version of kubernetes to deploy', default=DEFAULT_K8S_VERSION)
    parser.add_argument('--region', help='AWS region', default=DEFAULT_REGION)
    parser.add_argument('--zone',
                        help="AWS zone. If not specified the 'a' zone of the --region is used")
    parser.add_argument(
        '--nodes', help='number of worker nodes', type=int, default=1)
    parser.add_argument(
        '--node-size', help='instance type of the worker nodes', default=DEFAULT_NODE_SIZE)
    parser.add_argument(
        '--image', help='AMI identifier for all instances', default=DEFAULT_AMI)
    parser.add_argument(
        '--update-network-sg', help='update just the security group', action='store_true')
    parser.add_argument(
        '--container-log-max-size', help='max size of a container log file in MB',
        type=int, default=20)  # kops default is 10
    parser.add_argument(
        '--container-log-max-files', help='max number of container log files',
        type=int, default=5)
    parser.add_argument(
        '--csi', help='set up kubernetes cluster to use CSI driver',
        nargs='?', const='true', default='true', choices=['true', 'false'])
    return parser


def main():
    """main"""
    # set up user and group name from defaults
    user_name, group_name = KOPS_USER, KOPS_GROUP
    print "Nuvoloso deployment of K8S in AWS"

    args = make_parser_args().parse_args()
    state_store = "s3://" + args.kops_bucket
    if args.nodes <= 0:
        raise Exception("--nodes requires a positive value")
    if not args.zone:
        args.zone = args.region + 'a'

    print_start_summary(args, user_name, group_name, state_store)
    validate_dependencies()

    # validate SDK credentials available through environment
    # TBD a better way may be to have them set it in the credentials file
    if check_sdk_credentials() == 0:
        raise Exception(
            "Make sure AWS key and id environment variables are set to those provided for SDK")

    # Check to see if the credentials exist for the user already.  If they do, skip the
    # group/policy/user creation as we will assume that the user exists in AWS already.
    keyinfo = get_creds_from_file(user_name)
    if keyinfo is None:
        create_kops_user_group(user_name, group_name)

        keyinfo = create_and_store_key_info(user_name)
        if keyinfo is None:
            raise Exception(
                "Could not create key information for %s" % user_name)

        # Check to see if user exists
        print "Sleeping", AWS_USER_CREATE_DELAY, "seconds for user to propagate in AWS"
        time.sleep(AWS_USER_CREATE_DELAY)

    # switch credentials and create client with it for all future AWS operations
    key_id, secret = keyinfo['AccessKey']['AccessKeyId'], keyinfo['AccessKey']['SecretAccessKey']
    try:
        client = boto3.client('s3', aws_access_key_id=key_id,
                              aws_secret_access_key=secret,
                              region_name=args.region)
    except ClientError as exc:
        print "Could not create client using new credentials\nUnexpected error: %s" % exc
        return

    # create the bucket/store for kops
    kops_state_store(client, args)

    # reset access key/id for kops ops
    os.environ['AWS_ACCESS_KEY_ID'] = key_id
    os.environ['AWS_SECRET_ACCESS_KEY'] = secret

    if args.update_network_sg:
        # update the group and return
        update_network_security_group(user_name, args.cluster_name, args)
        return

    if args.csi == 'true':
        print "K8s deployment for CSI"
        if args.kube_version == DEFAULT_K8S_VERSION:
            args.kube_version = DEFAULT_K8S_VERSION_FOR_CSI
        if StrictVersion(args.kube_version) < StrictVersion(DEFAULT_K8S_VERSION_FOR_CSI):
            print "Minimum supported kubernetes version for CSI: %s" % DEFAULT_K8S_VERSION_FOR_CSI
            return
    if not validate_kops_version():
        print "Minimum supported kops version: %s" % DEFAULT_KOPS_VERSION
        return

    execute(args, state_store, user_name)


# launch the program
if __name__ == '__main__':
    main()
