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
Script to deploy a stand-alone postgres database using a nuvo vol backing store
for the purpose of testing static provisioning.

Dependencies:
- Create virtualenv
- pip install Jinja2
- kubectl and nvctl CLI are available in the PATH (or see environment below)
- kubectl has access to a configured Kubernetes cluster on AWS
- nuvoloso management and cluster are already deployed in kubernetes
- nvctl is configured via nvctl.config file to have access to the Nuvoloso
  management deployment
  - this configuration file must include authentication properties such as
    'LoginName'
- current directory is this directory

This script will do the following:
- If -V is not specified
  - authorize the account to have access to the 'General' service-plan
  - allocate sufficient capacity for the service plan for the account in
    a cluster
  - create a BOUND nuvo vol using a VolumeSeriesRequest
    - The volume uses 'General' service-plan (GP2)
    - if more than one cluster exists, --cluster-id can be specified to
      choose one
- create a PersistentVolume for the specified or created volume
- deploy postgres that uses the nuvo vol for its storage

Environment (optional):
NVCTL = full path to the nvctl executable
NVCTL_CONFIG_FILE = an alternate config file for NVCTL

To Do:
- support other service plans, storage types, volume sizes
- better provisioner naming for auto-created provisioner
- better volume name for auto-created volume
"""

import argparse
import json
import os
import subprocess
import tempfile
import time
import datetime
import yaml
from jinja2 import Template

DEFAULT_FS_TYPE = 'ext4'
XFS_FS_TYPE = 'xfs'
DEFAULT_TENANT_ADMIN_ACCOUNT = 'Demo Tenant'
SERVICE_PLAN = 'General'
VSR_TIMEOUT = '3m'
POD_TIMEOUT = 5  # minutes

POD_LABEL = 'test='
DEPLOYMENT_TEMPLATE = 'postgres-template.yaml.j2'
TEST_NS = 'deploy-test'  # account added as a suffix

GIB = 1024 * 1024 * 1024
VOLUME_SIZE = 1
DEFAULT_SPA_SIZE = 100 * GIB


def pretty_size(size):
    """
    Return human readable size as a string, eg '512GiB', for an integer size.
    """
    if size % 1024 == 0:
        for suffix in ['', 'KiB', 'MiB', 'GiB', 'TiB']:
            if size % 1024:
                return '%d%s' % (size, suffix)
            size /= 1024
        return '%d%s' % (size, 'PiB')
    if size % 1000 == 0:
        for suffix in ['', 'KB', 'MB', 'GB', 'TB']:
            if size % 1000:
                return '%d%s' % (size, suffix)
            size /= 1000
        return '%d%s' % (size, 'PB')
    return '%d%s' % (size, 'B')


def nvctl(*args, **kwargs):
    """
    Run the nvctl command with the specified args returning the output as
    a parsed json object by default. The kwargs support 'json=false' and
    'no_login=true'.
    Exception is raised for errors.
    """
    cmd_list = list(args)
    json_out = True
    if 'json' in kwargs and not kwargs['json']:
        json_out = False
    exe = os.environ.get('NVCTL')
    if exe is None:
        exe = 'nvctl'
    if 'no_login' in kwargs and kwargs['no_login']:
        cmd_list.insert(0, '--no-login')
    cmd_list.insert(0, exe)
    if json_out:
        cmd_list.extend(['-o', 'json'])

    code = 0
    j = None
    try:
        data = subprocess.check_output(cmd_list)
        if json_out:
            j = json.loads(data)
    except subprocess.CalledProcessError as exc:
        code = exc.returncode
        data = exc.output
        print 'error:', cmd_list, 'failed with exit code', code
        if data:
            print data
        raise
    except ValueError as exc:
        print 'invalid JSON'
        print data
        raise
    return j


def print_versions():
    """
    Print versions of kubectl and nvctl (and verifies that both exist)
    """
    cmd_list = ['kubectl', 'version']
    print 'executing', cmd_list
    subprocess.check_call(cmd_list)
    print ''

    exe = os.environ.get('NVCTL')
    if exe is None:
        exe = 'nvctl'
    cmd_list = [exe, 'version', '-o', 'json']
    print 'executing', cmd_list
    subprocess.check_call(cmd_list)
    print ''


def authenticate(no_login=False):
    """
    Verifies authentication has been performed.
    """
    if not no_login:
        auth_list = nvctl('auth', 'list')
        if not auth_list:
            print "Must login to continue:"
            print "# nvctl auth login --login=(username)"
            print "nvctl.config needs to be updated with:"
            print "LoginName = (username)"
            raise Exception('auth failed')


def find_cluster(flags, cid=None):
    """
    Find the cluster on which to operate and returns its JSON.
    If cid is specified, only the cluster with this ID is returned.
    Otherwise, if more than one cluster exists, one is returned arbitrarily.
    """
    j = nvctl('cluster', 'list', '-A', flags.authorizing_account,
              no_login=flags.no_login)
    for cluster in j:
        if cid is None:
            return cluster
        if cid == cluster['meta']['id']:
            return cluster
    if cid:
        raise Exception('no cluster found for id [%s]' % cid)
    raise Exception('no cluster found')


def find_domain(flags, did):
    """
    Find and return the domain with the specified ID did, returning its JSON.
    Exception is raised for errors.
    """
    j = nvctl('domain', 'list', '-A', flags.authorizing_account,
              no_login=flags.no_login)
    for domain in j:
        if did == domain['meta']['id']:
            return domain
    raise Exception('no CSP domain found for id [%s]' % did)


def find_spa(flags, domain, cluster):
    """
    Find a spa for the given cluster, account and service plan,
    returning its JSON or None.
    If more than one such pool exists, one is returned arbitrarily.
    """
    args = [
        'service-plan-allocation',
        '-A', flags.authorizing_account,
        'list', '-P', flags.plan,
        '-D', domain['name'],
        '-C', cluster['name']
    ]
    if flags.account != flags.authorizing_account:
        args.extend(['-Z', flags.account])
    else:
        args.extend(['--owner-auth'])

    j = nvctl(*args, no_login=flags.no_login)
    for spa in j:
        return spa
    return None


def allocate_capacity(flags, domain, cluster, size=DEFAULT_SPA_SIZE):
    """
    Allocate service plan capacity for an account in a cluster.
    The JSON for the resulting pool is returned.
    """
    args = [
        'vsr', 'create',
        '-O', 'ALLOCATE_CAPACITY',
        '-A', flags.authorizing_account,
        '-P', flags.plan,
        '-D', domain['name'],
        '-C', cluster['name'],
        '-b', pretty_size(size),
        '--complete-by', VSR_TIMEOUT
    ]
    if flags.account != flags.authorizing_account:
        args.extend(['-Z', flags.account])

    print 'Allocating capacity:', args
    j = nvctl(*args, no_login=flags.no_login)
    vsr = j[0]
    # need to wait for the VSR to complete
    return vsr_wait(flags, vsr, authorizing=True)


def find_volume(flags, vid):
    """
    Find the volume with id specified, or None.
    The JSON for the volume is returned if found.
    Exception is raised for errors.
    """
    j = nvctl('volume', 'list', '-A', flags.account_arg,
              no_login=flags.no_login)
    for volume in j:
        if vid == volume['meta']['id']:
            if volume['volumeSeriesState'] not in ['BOUND', 'PROVISIONED']:
                raise Exception('volume [%s] in state [%s] is not usable' %
                                (vid, volume['volumeSeriesState']))
            return volume
    return None


def publish_volume(flags, name):
    """
    Given a volumeID publish the volume in the cluster.
    """
    args = [
        'vsr', 'create',
        '--complete-by', VSR_TIMEOUT,
        '-O', 'PUBLISH',
        '-A', str(flags.account_arg),
        '-n', name,
    ]

    print 'Publishing volume:', args
    j = nvctl(*args, no_login=flags.no_login)
    vsr = j[0]

    # need to wait for the VSR to complete
    vsr_wait(flags, vsr)
    return


def create_volume(flags, domain, cluster, vol_size):
    """
    Given the JSON for the domain and cluster, create a volume
    and bind it to the cluster.
    The 'General' service plan is used.
    The requested size of the volume will 11GiB.
    """
    args = [
        'vsr', 'create',
        '--complete-by', VSR_TIMEOUT,
        '-O', 'CREATE', '-O', 'BIND',
        '-A', flags.account_arg,
        '-D', domain['name'],
        '-C', cluster['name'],
        '-n', str(flags.pvc_name),
        '-P', flags.plan,
        '-b', pretty_size(vol_size)
    ]

    print 'Creating volume:', args
    j = nvctl(*args, no_login=flags.no_login)
    vsr = j[0]

    # need to wait for the VSR to complete
    vsr = vsr_wait(flags, vsr)
    vol = find_volume(flags, vsr['volumeSeriesId'])
    if vol is None:
        raise Exception('error: VSR [ % s] SUCCEEDED but'
                        'cannot find volume [ % s]' %
                        (vsr['meta']['id'], vsr['volumeSeriesId']))
    return vol


def vsr_wait(flags, vsr, authorizing=False):
    """
    Wait for a VSR to terminate.
    """
    rid = vsr['meta']['id']
    print 'Waiting for VSR [%s] to complete...' % rid
    sleep_time = 0
    while vsr['volumeSeriesRequestState'] not in ['SUCCEEDED', 'FAILED',
                                                  'CANCELED']:
        time.sleep(min(sleep_time, 15))
        sleep_time += 5
        account = flags.account_arg
        if authorizing:
            account = flags.authorizing_account
        j = nvctl('vsr', 'get', '-A', account,
                  '--id', rid, no_login=flags.no_login)
        vsr = j[0]
    if vsr['volumeSeriesRequestState'] != 'SUCCEEDED':
        raise ValueError('error: VSR [%s] %s' %
                         (rid, vsr['volumeSeriesRequestState']))
    return vsr


def derive_file(flags, template, claim_name):
    """
    Read the template from the specified file, make substitutions
    and write it to a temp file, returning the temp file's name, ending in .yaml.
    """
    with open(template, 'r') as pvt:
        data = pvt.read()
    selector = None
    if flags.selector:
        parts = flags.selector.split('=')
        selector = "%s: %s" % (parts[0], parts[1])
    tmp = Template(data)
    res = tmp.render(namespace=flags.namespace,
                     name=flags.deployment_name,
                     claimname=claim_name,
                     selector=selector)
    with tempfile.NamedTemporaryFile(prefix=flags.deployment_name,
                                     suffix='.yaml', delete=False) as out:
        tmpname = out.name
        out.write(res)
    print 'created %s' % tmpname
    return tmpname


def get_secret_file(flags, domain, cluster):
    """
    Get secret config
    """
    outfile = os.path.join(os.getenv('TMPDIR', '/tmp'),
                           'secret-' + flags.account.lower().replace(' ', '-') + '.yaml')
    args = [
        'cluster', 'get-secret',
        '-D', domain['name'],
        '-n', cluster['name'],
        '-A', flags.account_arg,
        '-O', outfile
    ]

    print 'Getting Secret file:', args

    nvctl(*args, json=False, no_login=flags.no_login)
    file_object = open(outfile, "r")
    secret_yaml = yaml.load(file_object.read())
    file_object.close()

    secret_yaml['metadata']['namespace'] = flags.namespace

    file_object = open(outfile, "w")
    out_yaml = yaml.dump(secret_yaml)
    file_object.write(out_yaml)
    file_object.close()

    return outfile


def get_pvc_file(flags, vol, spa, pvc_name, dynamic):
    """
    Get pvc config
    """
    outfile = os.path.join(os.getenv('TMPDIR', '/tmp'),
                           pvc_name + '.yaml')
    if not dynamic:
        args = [
            'vs', 'get',
            '-A', flags.account_arg,
            '--id', vol['meta']['id'],
            '-K', 'k8sPvcYaml',
            '-O', outfile
        ]
    else:  # for dynamic need to get the pvc spec from spa
        args = [
            'spa', 'get',
            '-A', flags.account_arg,
            '--id', spa['meta']['id'],
            '-K', 'k8sPvcYaml',
            '-O', outfile
        ]

    print 'Getting PVC file:', args

    nvctl(*args, json=False, no_login=flags.no_login)
    file_object = open(outfile, "r")
    pvc_yaml = yaml.load(file_object.read())
    file_object.close()

    pvc_yaml['metadata']['namespace'] = flags.namespace
    pvc_yaml['metadata']['name'] = pvc_name

    file_object = open(outfile, "w")
    out_yaml = yaml.dump(pvc_yaml)
    file_object.write(out_yaml)
    file_object.close()

    return outfile


def kubectl_apply(file_name, remove=False):
    """
    Apply the kubernetes objects from the given file.
    If they are already created it will not cause an error.
    If remove is True, the file is removed after uploading.
    """
    cmd_list = ['kubectl', 'apply', '-f', file_name]
    print 'executing', cmd_list
    subprocess.check_call(cmd_list)
    if remove:
        os.remove(file_name)


def wait_for_pod(flags, timeout, *labels):
    """
    Wait for up to timeout minutes for the pod(s) matching the label(s) to be "Running"
    """
    print 'waiting for pods matching [%s]...' % ','.join(labels)
    cmd_list = ['kubectl', 'get', 'pods',
                '-n', flags.namespace, '-o', 'json', '-l', ','.join(labels)]
    for _ in range(0, 2 * timeout):
        time.sleep(30)
        try:
            data = subprocess.check_output(cmd_list)
            j = json.loads(data)
            all_running = True
            for pod in j['items']:
                phase = pod['status']['phase']
                if phase != 'Running':
                    all_running = False
                    break
            if all_running:
                return
            print 'pod status is [%s] waiting...' % phase
        except subprocess.CalledProcessError as exc:
            code = exc.returncode
            data = exc.output
            print 'error:', cmd_list, 'failed with exit code', code
            if data:
                print data
            raise
        except ValueError as exc:
            print 'invalid JSON'
            print data
            raise
    raise Exception(
        'timed out after %d minutes waiting for pod to be Running' % timeout)


def how_to_delete(vol, flags):
    """
    Print a message describing how to delete what just got created.
    """
    print ''
    print 'Success!'
    print 'To delete the kubernetes resources:'

    if not flags.dynamic:
        print '# kubectl delete pv nuvoloso-volume-%s' % vol['meta']['id']
        print 'To delete the volume series:'
        print "# nvctl -A '%s' vsr create -O DELETE -V %s" % (
            flags.account_arg, vol['meta']['id'])
        print 'It can be reused with this script by specifying -V id [%s]' % vol['meta']['id']
    else:
        print '    To unmount volume:'
        print '    # kubectl -n %s delete deployment %s' % (flags.namespace, flags.deployment_name)
        print '    To delete volume:'
        print '    # kubectl -n %s delete pvc %s' % (flags.namespace, flags.pvc_name)

    print 'To delete namespace:'
    print '# kubectl delete ns %s' % flags.namespace


def key_value(arg):
    """Check that an argument is in the form KEY=VALUE"""
    parts = arg.split('=')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("%r is not a KEY=VALUE" % arg)
    return arg


def get_parser_args():
    """
    Parse input arguments
    """
    parser = argparse.ArgumentParser(
        description="static provisioning test using postgres - see details in script")
    parser.add_argument('--no-login', action='store_true',
                        help='do not use credentials when running nvctl')
    parser.add_argument('-C', '--cluster-id',
                        help='a nuvo cluster id, default is to pick one')
    parser.add_argument('-V', '--volume-id',
                        help='a nuvo volume id to use rather than creating a new volume')
    parser.add_argument('-A', '--account',
                        help='name of the nuvo account that owns the volume, default is "%s"'
                        % DEFAULT_TENANT_ADMIN_ACCOUNT, default=DEFAULT_TENANT_ADMIN_ACCOUNT)
    parser.add_argument('-T', '--authorizing-account',
                        help='name of the nuvo tenant admin account, default is "%s"'
                        % DEFAULT_TENANT_ADMIN_ACCOUNT, default=DEFAULT_TENANT_ADMIN_ACCOUNT)
    parser.add_argument('-P', '--plan',
                        help='name of a service plan, default is "%s"' % SERVICE_PLAN,
                        default=SERVICE_PLAN)
    parser.add_argument('-t', '--fs-type',
                        help='filesystem type for the volume, default is "%s"' % DEFAULT_FS_TYPE,
                        choices=(DEFAULT_FS_TYPE, XFS_FS_TYPE),
                        default=DEFAULT_FS_TYPE)
    parser.add_argument('-D', '--dynamic', action='store_true',
                        help='also create a deployment using dynamic csi driver')
    parser.add_argument('-l', '--selector', metavar='KEY=VALUE', type=key_value,
                        help='The node selector to use. ' +
                        'When specified, the pod will be deployed only on a node with this label')
    parser.add_argument('-n', '--deployment-name',
                        help='the name of the deployment. If not specified a random name' +
                        ' will be generated')
    parser.add_argument('-p', '--pvc-name',
                        help='the name of the pvc. If not specified a random name will ' +
                        'be generated')
    parser.add_argument('-s', '--volume-size', type=int,
                        help='the volume size in GB. If not specified it defaults to 1 GB',
                        default=VOLUME_SIZE)
    def_ns = TEST_NS + "-" + DEFAULT_TENANT_ADMIN_ACCOUNT.lower().replace(' ', '-')
    parser.add_argument('-N', "--namespace",
                        help='the namespace where you wish to run the test. If not ' +
                        'specified it defaults to "' + def_ns + '"')
    return parser.parse_args()


def get_cluster_domain_spa(flags):
    """
    fetch the cluster, domain and spa objects
    """
    cluster = find_cluster(flags, flags.cluster_id)
    cluster_id = cluster['meta']['id']
    print 'Using cluster %s[%s]' % (cluster['name'], cluster_id)
    domain_id = cluster['cspDomainId']
    domain = find_domain(flags, domain_id)
    print 'Using domain %s[%s]' % (domain['name'], domain_id)
    spa = find_spa(flags, domain, cluster)
    return cluster, domain, spa


def is_csi_enabled():
    """
    Check the pods in the nuvoloso-cluster namespace for CSI.
    If the socket filepath is set return TRUE
    """
    p_1 = subprocess.Popen(['kubectl', '-n', 'nuvoloso-cluster', 'get',
                            'pods', '-o', 'json'], stdout=subprocess.PIPE)
    p_2 = subprocess.Popen(['grep', 'csi-socket', '-m', '1'],
                           stdin=p_1.stdout, stdout=subprocess.PIPE)
    p_2.communicate()
    if p_2.returncode:
        print "Using flex volume driver"
        return False
    print "Using CSI volume driver"
    return True


def main():
    """main
    """
    args = get_parser_args()

    print_versions()
    csi = is_csi_enabled()
    if not csi and args.dynamic:
        print "Must use CSI driver to enable dynamic deployment"
        return

    authenticate(args.no_login)
    args.account_arg = args.account
    if args.account != args.authorizing_account:
        args.account_arg = args.authorizing_account + '/' + args.account

    time_based_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    args.deployment_name = args.deployment_name if args.deployment_name != None \
        else "deploy-" + time_based_id
    args.pvc_name = args.pvc_name if args.pvc_name != None else "pvc-" + time_based_id
    args.volume_size *= GIB  # apply the documented multiplier
    args.namespace = args.namespace if args.namespace != None else TEST_NS + \
        "-" + args.account.lower().replace(' ', '-')

    vol = find_volume(args, args.volume_id)

    cluster, domain, spa = get_cluster_domain_spa(args)

    if vol is None:
        if spa is None:
            print 'No service plan capacity defined for "%s" for account "%s" on cluster "%s"' % \
                (args.plan, args.account, cluster['name'])
            allocate_capacity(args, domain, cluster)
            spa = find_spa(args, domain, cluster)
        if spa['reservableCapacityBytes'] < args.volume_size:
            print 'Increasing capacity of SPA [%s] by %s' % (spa['meta']['id'],
                                                             pretty_size(args.volume_size))
            allocate_capacity(args, domain, cluster,
                              spa['totalCapacityBytes'] + args.volume_size)

    if args.dynamic:
        print "Creating dynamic deployment"
        pvc_file = get_pvc_file(args, None, spa, args.pvc_name, True)
        deploy_file = derive_file(args, DEPLOYMENT_TEMPLATE,
                                  args.pvc_name)
    else:
        print "Creating static deployment"
        if vol is None:
            vol = create_volume(args, domain, cluster, args.volume_size)
        publish_volume(args, vol['name'])
        pvc_file = get_pvc_file(args, vol, spa, args.pvc_name, False)
        deploy_file = derive_file(args, DEPLOYMENT_TEMPLATE,
                                  args.pvc_name)
        print 'Using volume %s[%s]' % (vol['name'], vol['meta']['id'])

    kubectl_apply(deploy_file, False)
    kubectl_apply(pvc_file, False)
    print 'Created PVC- %s' % args.pvc_name
    if csi:
        secret_file = get_secret_file(args, domain, cluster)
        kubectl_apply(secret_file, False)
    wait_for_pod(args, POD_TIMEOUT, POD_LABEL + args.deployment_name)
    how_to_delete(vol, args)


# launch the program
if __name__ == '__main__':
    main()
