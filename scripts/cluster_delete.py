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

Cleans up a Nuvoloso cluster whose namespace has already been deleted from kubernetes.
All of the resources in the Nuvoloso configuration database related to the cluster are removed
except for storage and volume series requests, which must be terminated (or pass --fail-requests).
Volume series are transitioned to UNBOUND unless --delete-volumes is specified.

An attempt is made to delete corresponding CSP volumes via RELEASE storage-requests.
However, if this fails, a warning is issued and the remaining CSP Volume IDs are output.

Internal role is required to executed this script. The internal role is achieved either
by having access to the unix socket on which nvcentrald listens or by having trusted credentials.

See the usage for more information.
"""

import argparse
import datetime
import httplib
import json
import ssl
import socket
import sys
import time
import urllib

# timeout for RELEASE storage requests
RELEASE_TIMEOUT_SEC = 3 * 60

# time to poll storage requests (no python watcher)
POLL_SEC = 30


class CrudException(Exception):
    """An exception generated from a CRUD response"""

    def __init__(self, code, msg):
        self.code = code
        super(CrudException, self).__init__(msg)


def connect(args):
    """Connect to the server specified in the args.

    Parameters:
        args - the argparse.Namespace object with parsed arguments
    Returns:
        httplib.HTTPConnection object
    """

    if args.cert and args.key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        ctx.verify_mode = ssl.CERT_NONE
        ctx.load_cert_chain(certfile=args.cert, keyfile=args.key)

        conn = httplib.HTTPSConnection(
            host=args.host, port=args.port, timeout=30, context=ctx)
        conn.connect()
    else:
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(1)  # 1sec, should be instantaneous
        sock.connect(args.unix_socket)
        conn = httplib.HTTPConnection(
            host=args.host, port=args.port, timeout=30)
        conn.sock = sock

    # test the connection
    conn.request('GET', '/api/v1/system')
    resp = conn.getresponse()
    data1 = resp.read()
    if resp.status != 200:
        print resp.status, resp.reason
        print data1
        if resp.status == 403 and not (args.cert and args.key):
            print 'Suggestion: specify both --cert and --key options'
        sys.exit(1)
    return conn


def get_any(conn, resource_type, **kwargs):
    """Get any resources given the resource type and named args.

    Parameters:
        conn - the httplib.HTTPConnection
        resource_type - the simple nuvoloso API resource type
        kwargs - name value pairs to add as query parameters
    Returns:
        List of zero or more parsed JSON object
    """

    url = '/api/v1/%s' % resource_type
    if kwargs:
        params = []
        for key, value in kwargs.items():
            if isinstance(value, basestring):
                value = urllib.quote_plus(value)
            elif isinstance(value, bool):
                value = 'true' if int(value) else 'false'  # vs True or False
            params.append('%s=%s' % (urllib.quote_plus(key), value))
        url += '?' + '&'.join(params)
    conn.request('GET', url)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise CrudException(resp.status, 'Error for query %s(%s): Response: %d %s' %
                            (resource_type, kwargs, resp.status, body))
    return json.loads(body)


def get_one(conn, resource_type, **kwargs):
    """Get one resource given its resource type and named args.

    If exactly one resource is not returned for the query, an exception is thrown.

    Parameters:
        conn - the httplib.HTTPConnection
        resource_type - the simple nuvoloso API resource type
        kwargs - name value pairs to add as query parameters
    Returns:
        One parsed JSON object
    """

    obj_list = get_any(conn, resource_type, **kwargs)
    if len(obj_list) != 1:
        raise Exception('Error for %s(%s): Got %d objects in the response' %
                        (resource_type, kwargs, len(obj_list)))
    return obj_list[0]


def get_by_uuid(conn, resource_type, uuid):
    """Get one resource given its resource type and uuid.

    Parameters:
        conn - the httplib.HTTPConnection
        resource_type - the simple nuvoloso API resource type
        uuid - uuid of the object to return
    Returns:
        One parsed JSON object
    """

    url = '/api/v1/%s/%s' % (resource_type, uuid)
    conn.request('GET', url)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise CrudException(resp.status, 'Error for query %s[%s]: Response: %d %s' %
                            (resource_type, uuid, resp.status, body))
    return json.loads(body)


def delete_one(conn, resource_type, uuid):
    """Delete one resource given its ID.

    Parameters:
        conn - the httplib.HTTPConnection
        resource_type - the simple nuvoloso API resource type
        uuid - the UUID of the object to delete
    """

    url = '/api/v1/%s/%s' % (resource_type, uuid)
    conn.request('DELETE', url)
    resp = conn.getresponse()
    if resp.status != 204:
        body = resp.read()
        raise CrudException(resp.status, 'Error for delete %s(%s): Response: %d %s' %
                            (resource_type, uuid, resp.status, body))
    resp.close()
    print 'Deleted %s[%s]' % (resource_type, uuid)


def update_one(conn, resource_type, uuid, update_obj, version=None):
    """Update one resource. All attributes other than 'meta' in update_obj are set.

    Parameters:
        conn - the httplib.HTTPConnection
        resource_type - the simple nuvoloso API resource type
        uuid - the UUID of the object to update
        version - version to update
        update_obj - Object containing attributes to update.
    Returns:
        complete, updated object
    """

    url = '/api/v1/%s/%s' % (resource_type, uuid)
    params = []
    if version:
        params.append('version=%d' % version)
    for key in update_obj.keys():
        if key != 'meta':
            params.append('set=%s' % key)
    url += '?' + '&'.join(params)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json'}
    conn.request('PATCH', url, json.dumps(update_obj), headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise CrudException(resp.status, 'Error for update %s: Response: %d %s' %
                            (url, resp.status, body))
    return json.loads(body)


def deauthorize_plan_account(conn, plan_id, account_id):
    """Remove the specified account from the authorized accounts of the service plan.

    Error for invalid update is ignored as this is returned when the account is still in use.

    Parameters:
        conn - the httplib.HTTPConnection
        plan_id - service plan ID
        account_id - account to deauthorize
    """

    # special case of update, remove the authorized account
    url = '/api/v1/service-plans/%s?remove=accounts' % plan_id
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json'}
    update_obj = {'accounts': [account_id]}
    conn.request('PATCH', url, json.dumps(update_obj), headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    # 400 error returned when account is still in use
    if resp.status != 200 and resp.status != 400:
        raise CrudException(resp.status, 'Error for update %s: Response: %d %s' %
                            (url, resp.status, body))


def get_cluster(conn, args):
    """Get the cluster object.
    Raises an exception if the cluster is not in DEPLOYABLE, TIMED_OUT or TEAR_DOWN state.

    Parameters:
        conn - the httplib.HTTPConnection
        args - the argparse.Namespace object with parsed arguments
    Returns:
        parsed cluster JSON object
    """

    kwargs = {}
    account = None
    if args.account:
        account = get_one(conn, 'accounts', name=args.account)
    if args.domain:
        dom_args = {'name': args.domain}
        if account:
            dom_args['accountId'] = account['meta']['id']
        obj = get_one(conn, 'csp-domains', **dom_args)
        kwargs['cspDomainId'] = obj['meta']['id']
    if args.cluster_name:
        kwargs['name'] = args.cluster_name
    if account:
        kwargs['accountId'] = account['meta']['id']
    cluster = get_one(conn, 'clusters', **kwargs)
    if cluster['state'] not in ('DEPLOYABLE', 'TIMED_OUT', 'TEAR_DOWN'):
        raise Exception('Cluster %s in %s state cannot be deleted' %
                        (cluster['name'], cluster['state']))
    return cluster


def objects(obj_list):
    """Helper to return singular or plural of the word "object".

    Parameters:
        obj_list - any list
    Returns:
        string "object" or "objects" depending on the list length
    """

    ret = 'objects' if len(obj_list) != 1 else 'object'
    return ret


def create_sr(conn, new_obj):
    """Create a new storage-request.

    Parameters:
        conn - the httplib.HTTPConnection
        new_obj - Object containing attributes of the new storage-request
    Returns:
        Parsed created JSON object
    """
    resource_type = 'storage-requests'
    url = '/api/v1/%s' % (resource_type)
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json'}
    conn.request('POST', url, json.dumps(new_obj), headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 201:
        raise CrudException(resp.status, 'Error for POST %s: Response: %d %s' %
                            (resource_type, resp.status, body))
    return json.loads(body)


def fail_requests(conn, cluster, args):
    """Check if any active requests exist for the cluster.

    Marks requests as failed if args.fail_requests is True, otherwise raises an exception.
    Note: if additional requests get generated after this check, other than those the script
    itself creates, the script may still fail.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
        args - the argparse.Namespace object with parsed arguments
    """

    kwargs = {'clusterId': cluster['meta']['id'], 'isTerminated': False}
    sr_list = get_any(conn, 'storage-requests', **kwargs)
    vsr_list = get_any(conn, 'volume-series-requests', **kwargs)
    if not (sr_list or vsr_list):
        print 'No active requests detected for this cluster, continuing...'
        return

    if not args.fail_requests:
        totals = []
        if sr_list:
            totals.append('%d active storage-requests' % len(sr_list))
        if vsr_list:
            totals.append('%d active volume-series-requests' % len(vsr_list))
        raise Exception('%s for this cluster. Specify --fail-requests to proceed (unsafe)' %
                        ' and '.join(totals))

    now = datetime.datetime.utcnow().isoformat('T') + 'Z'
    for req in sr_list:
        update_obj = {'storageRequestState': 'FAILED'}
        messages = []
        if 'requestMessages' in req:
            messages = req['requestMessages']
        messages.append({
            'message': 'FAILED by cluster_delete script',
            'time': now,
        })
        update_obj['requestMessages'] = messages
        # no retries: if something is still changing it, abort
        update_one(conn, 'storage-requests', req['meta']['id'],
                   version=req['meta']['version'], update_obj=update_obj)
        print 'Set storage request[%s] state FAILED' % req['meta']['id']

    for req in vsr_list:
        update_obj = {'volumeSeriesRequestState': 'CANCELED'}
        messages = []
        if 'requestMessages' in req:
            messages = req['requestMessages']
        messages.append({
            'message': 'CANCELED by cluster_delete script',
            'time': now,
        })
        update_obj['requestMessages'] = messages
        # no retries: if something is still changing it, abort
        update_one(conn, 'volume-series-requests', req['meta']['id'],
                   version=req['meta']['version'], update_obj=update_obj)
        print 'Set volume series request[%s] state CANCELED' % req['meta']['id']

    if sr_list:
        print 'Marked %d storage-request %s as FAILED' % (len(sr_list), objects(sr_list))
    if vsr_list:
        print 'Marked %d volume-series-request %s as CANCELED' % (len(vsr_list), objects(vsr_list))


def delete_ag(conn, uuid):
    """Delete one application group object, ignoring not-exist and in-use errors.

    Parameters:
        conn - the httplib.HTTPConnection
        uuid - uuid of the application group object
    Returns:
        True if the object was deleted
    """

    try:
        delete_one(conn, 'application-groups', uuid)
        return True
    except CrudException as exc:
        if exc.code != 404 and exc.code != 409:
            raise
    return False


def delete_cg(conn, uuid):
    """Delete one consistency group object, ignoring not-exist and in-use errors.

    Parameters:
        conn - the httplib.HTTPConnection
        uuid - uuid of the consistency group object
    Returns:
        True and the list of application group IDs if the object was deleted
    """

    try:
        obj = get_by_uuid(conn, 'consistency-groups', uuid)
        delete_one(conn, 'consistency-groups', uuid)
        return True, obj['applicationGroupIds']
    except CrudException as exc:
        if exc.code != 404 and exc.code != 409:
            raise
    return False, []


def delete_snapshots(conn, vol):
    """Delete all snapshot objects for the given volume series.

    Parameters:
        conn - the httplib.HTTPConnection
        vol - parsed volume series JSON object
    """

    kwargs = {'volumeSeriesId': vol['meta']['id']}
    snap_list = get_any(conn, 'snapshots', **kwargs)
    for snap in snap_list:
        delete_one(conn, 'snapshots', snap['meta']['id'])
    print 'Deleted %d snapshot %s associated with volume series[%s]' % (
        len(snap_list), objects(snap_list), vol['meta']['id'])


def delete_volume_series(conn, cluster):
    """Delete all volume series bound to the given cluster.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    """

    kwargs = {'boundClusterId': cluster['meta']['id']}
    vs_list = get_any(conn, 'volume-series', **kwargs)
    cg_ids = set()
    for vol in vs_list:
        update_obj = {
            'volumeSeriesState': 'DELETING',
            'configuredNodeId': '',
            'rootStorageId': '',
            'mounts': [],
            'storageParcels': {},
            'capacityAllocations': {}
        }
        # no retries: if something is still changing it, abort
        update_one(conn, 'volume-series', vol['meta']['id'],
                   version=vol['meta']['version'], update_obj=update_obj)
        delete_snapshots(conn, vol)
        delete_one(conn, 'volume-series', vol['meta']['id'])
        cg_ids.add(vol['consistencyGroupId'])
    print 'Deleted %d volume series %s bound to the cluster' % (len(vs_list), objects(vs_list))

    deleted_cgs = []
    deleted_ags = []
    ag_ids = set()
    for uuid in cg_ids:
        deleted, cg_ags = delete_cg(conn, uuid)
        if deleted:
            deleted_cgs.append(uuid)
            ag_ids.update(cg_ags)
    print 'Deleted %d consistency group %s for the volume series' % \
        (len(deleted_cgs), objects(deleted_cgs))

    for uuid in ag_ids:
        deleted = delete_ag(conn, uuid)
        if deleted:
            deleted_ags.append(uuid)
    print 'Deleted %d application group %s for the consistency groups' % \
        (len(deleted_ags), objects(deleted_ags))


def unbind_volume_series(conn, cluster):
    """Unbind all volume series bound to the given cluster.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    """

    kwargs = {'boundClusterId': cluster['meta']['id']}
    vs_list = get_any(conn, 'volume-series', **kwargs)
    for vol in vs_list:
        vol['lifecycleManagementData']['finalSnapshotNeeded'] = False
        system_tags = []
        # systemTags with 'volume.cluster.' prefix are invalid unless bound
        for tag in vol['systemTags']:
            if not tag.startswith('volume.cluster.'):
                system_tags.append(tag)
        update_obj = {
            'volumeSeriesState': 'UNBOUND',
            'boundClusterId': '',
            'configuredNodeId': '',
            'rootStorageId': '',
            'servicePlanAllocationId': '',
            'clusterDescriptor': {},
            'mounts': [],
            'spaAdditionalBytes': 0,
            'storageParcels': {},
            'cacheAllocations': {},
            'capacityAllocations': {},
            'systemTags': system_tags,
            'lifecycleManagementData': vol['lifecycleManagementData']
        }
        # no retries: if something is still changing it, abort
        update_one(conn, 'volume-series', vol['meta']['id'],
                   version=vol['meta']['version'], update_obj=update_obj)
    print 'Unbound %d volume series %s bound to the cluster' % (len(vs_list), objects(vs_list))


def delete_storage(conn, cluster):
    """Delete all storage objects in the cluster.
    This function uses RELEASE storage-requests to attempt to delete the CSP volumes
    corresponding to the storage objects. A DETACH operation is added to storage-requests
    for storage objects whose state reflects that they are attached to nodes.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    """

    kwargs = {'clusterId': cluster['meta']['id']}
    storage_list = get_any(conn, 'storage', **kwargs)
    sr_list = []
    for obj in storage_list:
        obj_state = obj['storageState']
        ops = ['RELEASE']
        system_tags = []
        if obj_state['attachmentState'] != 'DETACHED' and obj_state['attachedNodeId']:
            ops = ['DETACH', 'RELEASE']
            system_tags = ['sr.forceDetachNodeID:%s' %
                           obj_state['attachedNodeId']]
        sr_obj = {
            'completeByTime': datetime.datetime.utcfromtimestamp(
                time.time() + RELEASE_TIMEOUT_SEC).isoformat('T') + 'Z',
            'storageId': obj['meta']['id'],
            'requestedOperations': ops,
            'systemTags': system_tags
        }
        sr_obj = create_sr(conn, sr_obj)
        sr_list.append(sr_obj)

    if sr_list:
        count = len(sr_list)
        print 'Waiting for %d storage-requests to RELEASE storage' % count
        # could use a watcher here, but that would require implementing a python watcher
        kwargs = {'clusterId': cluster['meta']['id'], 'isTerminated': False}
        abort_count = (RELEASE_TIMEOUT_SEC / POLL_SEC) + 1
        # delay a bit before entering the polling loop, typically saves 1 pass
        time.sleep(6)
        while count > 0:
            time.sleep(POLL_SEC)
            sr_list = get_any(conn, 'storage-requests', **kwargs)
            count = len(sr_list)
            abort_count -= 1
            if abort_count <= 0:
                raise Exception(
                    'Aborting! %d storage-requests have still not completed' % count)
            if count:
                print 'Still waiting for %d storage-requests to RELEASE storage' % count

    kwargs = {'clusterId': cluster['meta']['id']}
    storage_list = get_any(conn, 'storage', **kwargs)
    if storage_list:
        print 'WARNING! %d storage-requests failed, manual CSP volume cleanup required' % \
            len(storage_list)
    for obj in storage_list:
        obj_state = obj['storageState']
        sid = obj['storageIdentifier']
        obj_state['provisionedState'] = 'UNPROVISIONING' if sid else 'UNPROVISIONED'
        update_obj = {'storageState': obj_state}
        # no retries: if something is still changing it, abort
        update_one(conn, 'storage', obj['meta']['id'],
                   version=obj['meta']['version'], update_obj=update_obj)
        delete_one(conn, 'storage', obj['meta']['id'])
        if sid:
            print 'CSP Volume requiring manual detach and delete: %s' % sid


def delete_pool_storage(conn, cluster):
    """Deletes all storage associated with all pools associated with the cluster.

    The pools themselves are updated to have no reservations and are deleted separately.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    """

    delete_storage(conn, cluster)
    kwargs = {'clusterId': cluster['meta']['id']}
    pool_list = get_any(conn, 'pools', **kwargs)
    for obj in pool_list:
        update_obj = {
            'servicePlanReservations': {}
        }
        update_one(conn, 'pools', obj['meta']['id'],
                   version=obj['meta']['version'], update_obj=update_obj)


def delete_spas(conn, cluster, deauthorize):
    """Delete all service plan allocation objects related to the given cluster.

    Also attempts to update related service plan objects to remove authorized accounts
    if deauthorize is true.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
        deauthorize - if true, attempt to deauthorize service plans
    """

    kwargs = {'clusterId': cluster['meta']['id']}
    spa_list = get_any(conn, 'service-plan-allocations', **kwargs)
    for obj in spa_list:
        delete_one(conn, 'service-plan-allocations', obj['meta']['id'])
        if deauthorize:
            deauthorize_plan_account(
                conn, obj['servicePlanId'], obj['authorizedAccountId'])

    print 'Deleted %d service plan allocation %s associated with the cluster' % \
        (len(spa_list), objects(spa_list))


def delete_pools(conn, cluster):
    """Delete all pool objects related to the given cluster.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    """

    kwargs = {'clusterId': cluster['meta']['id']}
    pool_list = get_any(conn, 'pools', **kwargs)
    for obj in pool_list:
        delete_one(conn, 'pools', obj['meta']['id'])
    print 'Deleted %d pool %s associated with the cluster' % (len(pool_list), objects(pool_list))


def delete_nodes(conn, cluster):
    """Delete all node objects related to the given cluster.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    """

    kwargs = {'clusterId': cluster['meta']['id']}
    node_list = get_any(conn, 'nodes', **kwargs)
    for obj in node_list:
        delete_one(conn, 'nodes', obj['meta']['id'])
    print 'Deleted %d node %s associated with the cluster' % (len(node_list), objects(node_list))


def start_cluster_teardown(conn, cluster):
    """Change the state of the cluster to TEAR_DOWN.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    Returns:
        complete, updated cluster object
    """
    if cluster['state'] != 'TEAR_DOWN':
        update_obj = {
            'state': 'TEAR_DOWN'
        }
        # no retries: if something is still changing it, abort
        cluster = update_one(conn, 'clusters', cluster['meta']['id'],
                             version=cluster['meta']['version'], update_obj=update_obj)
    print 'Cluster %s transitioned to TEAR_DOWN state' % (cluster['name'])
    return cluster


def delete_cluster(conn, cluster):
    """Delete the cluster resource.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
    """
    delete_one(conn, 'clusters', cluster['meta']['id'])
    print 'Deleted cluster object %s[%s]' % (cluster['name'], cluster['meta']['id'])


def delete_all(conn, cluster, delete_volumes):
    """Delete all resources related to the given cluster.

    Parameters:
        conn - the httplib.HTTPConnection
        cluster - parsed cluster JSON object
        delete_volumes - if true, volumes are deleted, otherwise they are unbound
    """

    if delete_volumes:
        delete_volume_series(conn, cluster)
    else:
        unbind_volume_series(conn, cluster)
    delete_pool_storage(conn, cluster)
    delete_spas(conn, cluster, delete_volumes)
    delete_pools(conn, cluster)
    delete_nodes(conn, cluster)
    delete_cluster(conn, cluster)


def main():
    """main
    """

    parser = argparse.ArgumentParser(
        description='Delete a Nuvoloso cluster and related Nuvoloso resources. ' +
        'The cluster must be in DEPLOYABLE, TIMED_OUT or TEAR_DOWN state. ' +
        'Volume series bound to the cluster will be transitioned to the UNBOUND state ' +
        'and the underlying block storage will be released; snapshots are retained. ' +
        'By default the script connects to the local nvcentrald via a unix socket. ' +
        'See the options for other connection methods.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-E', '--cert', help='The certificate to use for secure connections')
    parser.add_argument(
        '--key', help='The private key to use for secure connections')
    parser.add_argument('--host', help='The management service host',
                        default='localhost')
    parser.add_argument('--port', help='The management service port number',
                        type=int, default=443)
    parser.add_argument('--unix-socket', metavar='PATH',
                        help='Unix domain socket to use instead of using the network',
                        default='/var/run/nuvoloso/nvcentrald.sock')
    parser.add_argument(
        '-A', '--account', help='Name of the account that owns the domain and cluster')
    parser.add_argument('-D', '--domain',
                        help='Name of a cloud service provider domain')
    parser.add_argument('-C', '--cluster-name',
                        help='Name of a cluster in the specified domain')
    parser.add_argument('--delete-volumes', action='store_true',
                        help='Permanently delete all volume-series bound to the cluster, '
                        'including their snapshots, consistency groups and application groups')
    parser.add_argument('--fail-requests', action='store_true',
                        help='This option is best-effort. Nuvoloso may be '
                        'processing active requests. Marks active storage and volume series '
                        'requests associated with the cluster as failed. Otherwise, '
                        'active requests will cause the script to fail')
    parser.add_argument('-y', '--confirm', action='store_true',
                        help='Confirm the deletion of the cluster')
    args = parser.parse_args()
    conn = connect(args)
    cluster = get_cluster(conn, args)
    if not args.confirm:
        raise Exception('specify --confirm to delete the cluster')
    cluster = start_cluster_teardown(conn, cluster)
    fail_requests(conn, cluster, args)
    delete_all(conn, cluster, args.delete_volumes)
    conn.close()


# launch the program
if __name__ == '__main__':
    main()
