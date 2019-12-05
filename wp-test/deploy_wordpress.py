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
Script to deploy a wordpress application using multiple nuvo vols.

Uses dynamic provisioning only
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
- must specify a parent account and a subordinate account

This script will do the following:
  - authorize the account to have access to the 'General' service-plan
  - allocate sufficient capacity for the service plan for the account in
    a cluster
  - fetch and create the appropriate secret and pvcs
  - deploy two pods for the wordpress and mysql
"""
import argparse
import subprocess
import os
import tempfile
import json
import time
import datetime
import yaml
from jinja2 import Template

DEFAULT_TENANT_ADMIN_ACCOUNT = 'Demo Tenant'
SERVICE_PLAN = 'General'
VSR_TIMEOUT = '3m'
POD_TIMEOUT = 5  # minutes

GIB = 1024 * 1024 * 1024
VOLUME_SIZE = 1
DEFAULT_SPA_SIZE = 100 * GIB

TEST_NS = 'deploy-test'  # account added as a suffix

DEPLOYMENT_TEMPLATE = 'wordpress-template.yaml'
POD_LABEL = 'app='


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
        'service-plan-allocation', 'list',
        '-A', flags.authorizing_account,
        '-P', flags.plan,
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


def get_pvc_file(flags, spa, pvc_name):
    """
    Get pvc config
    """
    outfile = os.path.join(os.getenv('TMPDIR', '/tmp'),
                           pvc_name + '.yaml')

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
    pvc_yaml['metadata'].update({'labels': {'app': flags.app_label}})

    file_object = open(outfile, "w")
    out_yaml = yaml.dump(pvc_yaml)
    file_object.write(out_yaml)
    file_object.close()

    return outfile


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


def derive_file(flags, template, wpname, sqlname):
    """
    Read the template from the specified file, make substitutions
    and write it to a temp file, returning the temp file's name, ending in .yaml.
    """
    with open(template, 'r') as pvt:
        data = pvt.read()
    tmp = Template(data)
    res = tmp.render(namespace=flags.namespace,
                     wpname=wpname,
                     sqlname=sqlname,
                     sqlclaimname=flags.sql_pvc,
                     wpclaimname=flags.wp_pvc,
                     applabel=flags.app_label,
                     cgname=flags.cg_name,
                     cgdescription=flags.cg_description,
                     cgtags=flags.cg_tag_list,
                     agname=flags.ag_name,
                     agdescription=flags.ag_description,
                     agtags=flags.ag_tag_list)
    with tempfile.NamedTemporaryFile(prefix=flags.deployment_name,
                                     suffix='.yaml', delete=False) as out:
        tmpname = out.name
        out.write(res)
    print 'created %s' % tmpname
    return tmpname


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


def how_to_use(flags, wpname):
    """
    Print a message describing how to use the wordpress app
    """
    print ''
    print 'Success!'
    print 'To use wordpress enter the hostname into a browser:'
    cmd = 'kubectl -n ' + flags.namespace + \
        ' get services ' + wpname + ' -o json | grep hostname'
    output = subprocess.check_output(cmd, shell=True)
    print output
    print 'This may take a few minutes to become available.'


def how_to_delete(flags, wpname, sqlname):
    """
    Print a message describing how to delete what just got created.
    """
    print ''
    print 'Success!'
    print 'To delete the kubernetes resources:'

    print '    To unmount volumes:'
    print '    # kubectl -n %s delete deployment %s' % (flags.namespace, wpname)
    print '    # kubectl -n %s delete deployment %s' % (flags.namespace, sqlname)
    print '    To delete volume:'
    print '    # kubectl -n %s delete pvc %s' % (flags.namespace, flags.wp_pvc)
    print '    # kubectl -n %s delete pvc %s' % (flags.namespace, flags.sql_pvc)

    print 'To delete namespace:'
    print '# kubectl delete ns %s' % flags.namespace


def get_parser_args():
    """
    Parse input arguments
    """
    parser = argparse.ArgumentParser(
        description="dynamic provisioning test using wordpress - see details in script")
    parser.add_argument('--no-login', action='store_true',
                        help='do not use credentials when running nvctl')
    parser.add_argument('-C', '--cluster-id',
                        help='a nuvo cluster id, default is to pick one')
    parser.add_argument('-A', '--account',
                        help='name of the nuvo account that owns the volume',
                        required=True)
    parser.add_argument('-T', '--authorizing-account',
                        help='name of the nuvo tenant admin account',
                        required=True)
    parser.add_argument('-P', '--plan',
                        help='name of a service plan, default is "%s"' % SERVICE_PLAN,
                        default=SERVICE_PLAN)
    parser.add_argument('-n', '--deployment-name',
                        help='the name of the deployment. If not specified a random name' +
                        ' will be generated')
    parser.add_argument('-s', '--volume-size', type=int,
                        help='the volume size in GB. If not specified it defaults to 1 GB',
                        default=VOLUME_SIZE)
    parser.add_argument('-N', "--namespace",
                        help='the namespace where you wish to run the test.')
    parser.add_argument('-m', '--sql-pvc',
                        help='the name of the mysql pvc. If not specified a random name will ' +
                        'be generated')
    parser.add_argument('-w', '--wp-pvc',
                        help='the name of the wordpress pvc. If not specified a random name will ' +
                        'be generated')
    parser.add_argument('-l', '--app-label',
                        help='the app label. If not specified a random name will ' +
                        'be generated')
    parser.add_argument('-a', '--ag-name',
                        help='the ag-name')
    parser.add_argument('-e', '--ag-description',
                        help='the ag-description')
    parser.add_argument('-f', '--ag-tag-list', action='append',
                        help='the ag-tag list')
    parser.add_argument('-c', '--cg-name',
                        help='the cg-name')
    parser.add_argument('-d', '--cg-description',
                        help='the cg-description')
    parser.add_argument('-t', '--cg-tag-list', action='append',
                        help='the cg-tag list')
    parser.add_argument('-y', '--dry-run', action='store_true',
                        help='dry run to see if yaml is parsed correctly')
    return parser.parse_args()


def main():
    """
    main
    """
    args = get_parser_args()
    print_versions()

    authenticate(args.no_login)
    args.account_arg = args.account
    if args.account != args.authorizing_account:
        args.account_arg = args.authorizing_account + '/' + args.account

    time_based_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    args.deployment_name = args.deployment_name if args.deployment_name != None \
        else "wpDeploy-" + time_based_id
    args.volume_size *= GIB  # apply the documented multiplier
    args.namespace = args.namespace if args.namespace != None else TEST_NS + \
        "-" + args.account.lower().replace(' ', '-')
    args.sql_pvc = args.sql_pvc if args.sql_pvc != None else "sql-pvc-" + time_based_id
    args.wp_pvc = args.wp_pvc if args.wp_pvc != None else "wp-pvc-" + time_based_id
    args.app_label = args.app_label if args.app_label != None else "wordpress-" + time_based_id
    wpname = 'wp-' + args.app_label
    sqlname = 'sql-' + args.app_label
    cluster, domain, spa = get_cluster_domain_spa(args)
    print 'spa', spa

    if args.dry_run:
        modded_file = derive_file(args, DEPLOYMENT_TEMPLATE, wpname, sqlname)
        file_out = open(modded_file, "r")
        text = file_out.read()
        print text
        file_out.close()
        exit()

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

    sql_pvc_file = get_pvc_file(args, spa, args.sql_pvc)
    wp_pvc_file = get_pvc_file(args, spa, args.wp_pvc)
    secret_file = get_secret_file(args, domain, cluster)
    deploy_file = derive_file(args, DEPLOYMENT_TEMPLATE, wpname, sqlname)
    kubectl_apply(deploy_file, False)
    kubectl_apply(secret_file, False)
    kubectl_apply(wp_pvc_file, False)
    kubectl_apply(sql_pvc_file, False)
    print args
    wait_for_pod(args, POD_TIMEOUT, POD_LABEL + args.app_label)
    how_to_use(args, wpname)
    how_to_delete(args, wpname, sqlname)


# launch the program
if __name__ == '__main__':
    main()
