#!/usr/bin/env python2.7
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
This tool processes an agentd.log file and produces a script to reproduce the nuvo API calls.
This tool should be able to process both agentd.log and agentd-json.log files.
The output script should be runnable, however it's more likely than not that hand editing
will be necessary, in particular to change the devices.
There are inline comments added to the script output when a failed nuvo api command is found
in the log file.
A new run() function is created each time a new startup of the nuvo process is detected.

This tool is sensitive to changes and additions to the Nuvo API and the format of agentd.log.
When changes are made this tool may break or produce incorrect output.
"""
import sys
import os
import re

#GetVolumeManifest is the agentd.log name for the Manifest API.
API_DICT = {'FormatDevice': {'cmd': 'format-device', \
            'params': [' --device-uuid ', ' --device ', ' --parcel-size ']}, \
            'UseDevice': {'cmd': 'use-device', 'params': [' --device-uuid ', ' --device ']}, \
            'CloseDevice': {'cmd': 'close-device', 'params': [' --device-uuid ']}, \
            'NodeLocation': {'cmd': 'node-location', \
            'params': [' --node-uuid ', ' --ipv4-addr ', ' --port ']}, \
            'DeviceLocation': {'cmd': 'device-location', \
            'params': [' --device-uuid ', ' --node-uuid ']}, \
            'UseCacheDevice': {'cmd': 'use-cache-device', \
            'params': [' --device-uuid ', ' --device ']}, \
            'AllocCache': {'cmd': 'alloc-cache', 'params': [' --vol-series ', ' --number ']},
            'CreateLogVol': {'cmd': 'create-volume', \
            'params': [' --vol-series ', ' --root-device ', ' --root-parcel ', ' --size ']}, \
            'DestroyVol': {'cmd': 'destroy-volume', \
            'params': [' --vol-series ', ' --root-device ', ' --root-parcel ']}, \
            'ExportLun': {'cmd': 'export', \
            'params': [' --vol-series ', ' --pit ', ' --export-name ', ' --readonly ']}, \
            'UnexportLun': {'cmd': 'unexport', \
            'params': [' --vol-series ', ' --pit ', ' --export-name ']}, \
            'AllocParcels': {'cmd': 'alloc-parcels', \
            'params': [' --vol-series ', ' --device-uuid ', ' --number ']}, \
            'CloseVol': {'cmd': 'close-volume', 'params': [' --vol-series ']}, \
            'OpenVol': {'cmd': 'open-volume', \
            'params': [' --vol-series ', ' --root-device ', ' --root-parcel ']}, \
            'CreatePit': {'cmd': 'create-pit', 'params': [' --vol-uuid ', ' --pit-uuid ']}, \
            'DeletePit': {'cmd': 'delete-pit', 'params': [' --vol-uuid ', ' --pit-uuid ']}, \
            'PauseIo': {'cmd': 'pause-io', 'params': [' --vol-uuid ']}, \
            'ResumeIo': {'cmd': 'resume-io', 'params': [' --vol-uuid ']}, \
            'ListPits': {'cmd': 'list-pits', 'params': [' --vol-uuid ']}, \
            'GetStats': {'cmd': 'get-stats', \
            'params': ['is_device', 'is_read', ' --clear ', ' --device-uuid ']}, \
            'GetVolumeStats': {'cmd': 'get-vol-stats', \
            'params': [' --clear ', ' --volume-uuid ']}, \
            'LogLevel': {'cmd': 'log-level', 'params': [' --module-name ', ' --log-level ']}, \
            'LogSummary': {'cmd': 'log-summary', \
            'params': [' --volume ', ' --parcel-index ', ' --segment-index ']}, \
            'GetVolumeManifest': {'cmd': 'manifest', \
            'params': [' --short ', ' --volume-uuid ', ' --file-name ']}}

NUVO_CMD = '$NUVO_VM_CMD '
DEF_VARS = {'NUVO_SOCKET': '"/var/run/nuvoloso/nuvo.sock"',
            'NUVO_VM_PATH': '"."',
            'NUVO_VM_CMD': '"sudo $NUVO_VM_PATH/nuvo_vm -s $NUVO_SOCKET"',
            'NUVO_FUSE_DIR' : '"/var/local/nuvoloso"',
            'MOUNT_DIR' : '"/mnt"'}
V_UUIDS = {}
PIT_UUIDS = {}
D_DEVICES = {}
E_NAMES = {}
D_RD_STATS = {}
D_WR_STATS = {}
V_RD_STATS = {}
V_WR_STATS = {}
COMMANDS = []
SUPPRESS_STATS = True
SUB_VOLUME_AND_PIT_UUIDS = True

def main():
    """ Processes the log file
    """
    if len(sys.argv) != 2:
        usage()
        sys.exit()
    else:
        filepath = sys.argv[1]
        if not os.path.isfile(filepath):
            sys.stderr.write("Input file not found.\n")
            usage()
            sys.exit()

    print "#!/bin/bash\n"
    print_vars()
    print_mount_fns()
    print "\n# Device Map\n# Substitute device paths for local environment"

    startups = 0
    with open(filepath) as filep:
        line_str = filep.readline()
        while line_str:
            startups = process_line(line_str, startups)
            line_str = filep.readline()

    # At the end of the log file. End the last run function.
    cmd = "\tset +ex\n\techo 'End of nuvo_vm commands'\n"
    cmd += "}} # End nuvo_run{}()\n".format(startups - 1)
    COMMANDS.append(cmd)

    print_v_uuids()
    print_pit_uuids()
    print_d_stats()
    print_v_stats()

    # If a partial agentd.log is provided the script may not be runnable
    if startups == 0:
        sys.stderr.write("Warning. The agentd.log file was incomplete.\n")
        print "\n# Warning. The agentd.log file was incomplete\n"
        print "# Partial command output is available below"
        print "partial_nuvo_run{}() {{\n".format(startups)
    elif startups > 1:
        print "\n# Agentd.log shows the nuvo process was restarted {} times".format(startups - 1)

    for cmd in COMMANDS:
        print cmd

    if startups > 0:
        print "\n# Reproduces the first configuration found in the log"
        print "# Change the function to the run you want to reproduce"
        print "nuvo_run0\n"


def usage():
    """ Prints a usage statement
    """
    sys.stderr.write("Usage:\n")
    sys.stderr.write("  agentdlog2cmd.py agentd.log\n")
    sys.stderr.write("Description:\n")
    sys.stderr.write("  This tool processes an agentd.log file and produces a script to\n")
    sys.stderr.write("  reproduce the sequence nuvo API calls.\n")
    sys.stderr.write("  This tool can process both agentd.log and agentd-json.log files.\n")
    sys.stderr.write("  If you have multiple agentd.log files they should be concatenated\n")
    sys.stderr.write("  before running the tool, otherwise the output may be incomplete.\n")
    sys.stderr.write("  There are inline comments added to the script output when a failed\n")
    sys.stderr.write("  command was encountered.\n")
    sys.stderr.write("  A new run() function is created each time a new startup of the nuvo\n")
    sys.stderr.write("  process is detected.\n")
    sys.stderr.write("  By default the script will execute only the run0() function.\n")
    sys.stderr.write("Known Issues:\n")
    sys.stderr.write("  The entire agentd.log file is processed before commands are output,\n")
    sys.stderr.write("  while processing the command may appear hung.\n")
    sys.stderr.write("  Statistics are calculated across all runs.\n")
    sys.stderr.write("  Volume statistics are incomplete.\n")
    sys.stderr.write("  Detection of errors in agentd.log is limited to the nuvo API commands.\n")
    sys.stderr.write("  Some errors may not be detected.\n")
    sys.stderr.write("  Scripting multinode configurations is not supported.\n")
    sys.stderr.write("  You will need separate scripts generated for each node.\n")
    sys.stderr.write("  This tool is sensitive to changes and additions to the Nuvo API\n")
    sys.stderr.write("  and the format of agentd.log. When changes are made this tool may break\n")
    sys.stderr.write("  or produce incorrect output.\n")

def print_mount_fns():
    """Adds a boilerplate mount function to the script
    """
    print "\nmount_volume() {"
    print "\tsudo mkdir $MOUNT_DIR/${1}"
    print "\tsudo blkid $NUVO_FUSE_DIR/${2} >/dev/null || sudo mkfs -t ext4 $NUVO_FUSE_DIR/${2}"
    print "\tsudo mount $NUVO_FUSE_DIR/${2} $MOUNT_DIR/${1}"
    print "}\n"
    print "umount_volume() {"
    print "\tsudo umount $MOUNT_DIR/${1}"
    print "}\n"

def print_d_stats():
    """Prints a comment with the device statistics summary
    """
    print "\n# Device Statistics"
    for uuid in D_RD_STATS:
        print "# {}".format(uuid)
        if int(D_RD_STATS[uuid][0]) > 0:
            print "#  Reads: {a:8d}\t Avg. IO Size: {b:6d}\t Total Bytes: {c:16d}".format(\
            a=int(D_RD_STATS[uuid][0]), \
            b=int((int(D_RD_STATS[uuid][1])/int(D_RD_STATS[uuid][0]))), \
            c=int(D_RD_STATS[uuid][1]))
        else:
            print "#  Reads:        0\t Avg. IO Size:      0\t Total Bytes:                0"

        if int(D_WR_STATS[uuid][0]) > 0:
            print "# Writes: {a:8d}\t Avg. IO Size: {b:6d}\t Total Bytes: {c:16d}".format(\
            a=int(D_WR_STATS[uuid][0]), \
            b=int((int(D_WR_STATS[uuid][1])/int(D_WR_STATS[uuid][0]))), \
            c=int(D_WR_STATS[uuid][1]))
        else:
            print "#  Reads:        0\t Avg. IO Size:      0\t Total Bytes:                0"

def print_v_stats():
    """Prints a comment with the volume statistics summary
    """
    print "\n# Volume Statistics"
    for uuid in V_RD_STATS:
        print "# {}".format(uuid)
        if int(V_RD_STATS[uuid][0]) > 0:
            print "#  Reads: {a:8d}\t Avg. IO Size: {b:6d}\t Total Bytes: {c:16d}".format(\
            a=int(V_RD_STATS[uuid][0]), \
            b=int((int(V_RD_STATS[uuid][1])/int(V_RD_STATS[uuid][0]))), \
            c=int(V_RD_STATS[uuid][1]))
        else:
            print "#  Reads:        0\t Avg. IO Size:      0\t Total Bytes:                0"

        if int(V_WR_STATS[uuid][0]) > 0:
            print "# Writes: {a:8d}\t Avg. IO Size: {b:6d}\t Total Bytes: {c:16d}".format(\
            a=int(V_WR_STATS[uuid][0]), \
            b=int((int(V_WR_STATS[uuid][1])/int(V_WR_STATS[uuid][0]))), \
            c=int(V_WR_STATS[uuid][1]))
        else:
            print "#  Reads:        0\t Avg. IO Size:      0\t Total Bytes:                0"


def print_vars():
    """Adds the default environment variables to the script output
    """
    print "# Default variables"
    for key, val in DEF_VARS.items():
        print "{}={}".format(key, val)

def print_v_uuids():
    """Prints the assigned $VOL{#} variable for each volume UUID.
    """
    if not SUB_VOLUME_AND_PIT_UUIDS:
        return
    print "\n# Volume UUID Substitution"
    for uuid in V_UUIDS:
        print "{}=$(uuidgen)".format(V_UUIDS[uuid])

def print_pit_uuids():
    """Prints the assigned $PIT{#} variable for each PiT UUID.
    """
    if not SUB_VOLUME_AND_PIT_UUIDS:
        return
    print "\n# PiT UUID Substitution"
    for uuid in PIT_UUIDS:
        print "{}=$(uuidgen)".format(PIT_UUIDS[uuid])

def pit_sub(uuid):
    """Stores the UUID in a script PIT{#} variable
    """
    if not SUB_VOLUME_AND_PIT_UUIDS:
        return uuid

    if uuid in PIT_UUIDS:
        p_var = PIT_UUIDS[uuid]
    else:
        p_var = 'PIT{}'.format(len(PIT_UUIDS))
        PIT_UUIDS[uuid] = p_var
    return '$' + p_var

def e_name(uuid, name):
    """Stores the export name associated with a volume or pit UUID
    """
    if not SUB_VOLUME_AND_PIT_UUIDS:
        E_NAMES[uuid] = name
    elif uuid in V_UUIDS or uuid in PIT_UUIDS:
        E_NAMES[uuid] = name
    else:
        print "Parse error"
    return name

def v_sub(uuid):
    """Stores the UUID in a script VOL{#} variable
    """
    if not SUB_VOLUME_AND_PIT_UUIDS:
        return uuid

    if uuid in V_UUIDS:
        v_var = V_UUIDS[uuid]
    else:
        v_var = 'VOL{}'.format(len(V_UUIDS))
        V_UUIDS[uuid] = v_var
    return '$' + v_var

def d_sub(d_path):
    """Stores the device path name in the DEV_MAP variable
    """
    basename = os.path.basename(d_path)
    if basename in D_DEVICES:
        d_var = D_DEVICES[basename]
    else:
        decl_var = 'DEV_MAP[\"' + basename + '\"]=\'' + d_path + '\''
        d_var = '${DEV_MAP[\"' + basename + '\"]}'
        D_DEVICES[basename] = d_var
        print "{}".format(decl_var)
    return d_var

def add_mount_cmd(api, v_uuid):
    """Adds a mount or umount command to the script
    """
    if api == 'ExportLun':
        cmd = "\n\tmount_volume " + v_sub(v_uuid) + " " + E_NAMES[v_uuid]
    elif api == 'UnexportLun':
        cmd = "\n\tumount_volume " + v_sub(v_uuid)
    return cmd

def cmd_status_check(status, line_str):
    """Checks a line for a status message

       Parameters:
            status - the status string
            line_str - line string
    """
    regex = re.escape(status)
    return bool(re.search(regex, line_str))


def cmd_status(line_str, api):
    """Check if the log line is an API call or API return.
       API calls generally have nothing after the parameters list.
       GetStats API calls have either R or W appended the line.
       Lines with either "failed" or "succeeded" appended are results.

       Parameters:
            line_str - line string
            api - the api string
    """
    if api == 'GetStats':
        regex = r"\(.*\).(R|W)$"
        if re.search(regex, line_str):
            return ''
        return 'failed'
    elif cmd_status_check('error', line_str):
        return 'failed'
    elif cmd_status_check('failed', line_str):
        return 'failed'
    elif cmd_status_check('succeeded', line_str):
        return 'succeeded'
    return ''

def get_params(line_str):
    """ A NUVOAPI entry in agentd.log is generally in the following form:
        NUVOAPI ApiName(param1, param2, ...)
        This stores each of the parameters in the param array

       Parameters:
            line_str - line string
    """
    params = []
    regex = r"\(.*\)"
    match = re.search(regex, line_str)
    if match:
        pvars = match.group().split(',')
        num_pvars = len(pvars)
        if num_pvars == 0:
            sys.stderr.write("fatal parsing error. exiting")
            sys.exit()
        elif num_pvars == 1:
            params.append(pvars[0].strip('(').split(')', 1)[0].strip(')'))
        else:
            params.append(pvars[0].strip('('))
            for pnum in range(1, num_pvars - 1):
                params.append(pvars[pnum].strip())
            params.append(pvars[num_pvars - 1].split(')', 1)[0].strip(')').strip())
    return params

def process_line(raw_line_str, startups):
    """Takes a line from the agentd.log file and searches for a nuvo api command.

       Parameters:
            line_str - line string
    """
    # Remove \n from strings
    regex = r"\\n"
    line_str = re.sub(regex, '', raw_line_str)

    # This message in the log means agentd gave up on nuvo
    regex = r"NUVOAPI NOT INITIALIZED"
    match = re.search(regex, line_str)
    if match:
        msg = "\t# Agentd reported that the nuvo process was no longer responding."
        COMMANDS.append(msg)
        return startups

    # UseNodeUUID can be called several times before succeeding.
    # We only care about the successful attempt. Discard others.
    regex = r"Successfully set nuvo service node UUID.*"
    match = re.search(regex, line_str)
    if match:
        if startups > 0:
            # End the previous run function
            cmd = "\tset +ex\n\techo 'End of nuvo_vm commands'\n"
            cmd += "}} # End nuvo_run{}()\n".format(startups - 1)
            COMMANDS.append(cmd)
            msg = "# Restart {}\n".format(startups)
        else:
            msg = "\n# Initial Startup\n"

        # Start a new run function
        cmd = msg + "nuvo_run{}() {{\n".format(startups)
        cmd += "\tset -ex"
        startups += 1
        COMMANDS.append(cmd)

        line_str = match.group()
        regex = r"\[.*\]"
        match = re.search(regex, line_str)
        n_uuid = (match.group()).strip('[').strip(']')
        cmd = "\t" + NUVO_CMD + "use-node-uuid -u " + n_uuid
        COMMANDS.append(cmd)
        return startups

    if process_metrics('Storage', line_str):
        return startups
    if process_metrics('Volume', line_str):
        return startups
    process_nuvo_api_command(line_str)
    return startups

def process_metrics(metric_type, line_str):
    """Handle Storage and Volume metrics

       Parameters:
            metric_type - the metric type
            line_str - line string
    """
    if metric_type == 'Storage':
        regex = r"NUVOAPI Metrics on Storage.*"
    else:
        regex = r"NUVOAPI Metrics on Volume.*"
    match = re.search(regex, line_str)
    if match:
        uuid = (match.group()).split(metric_type)[1].strip().split(' ', 1)[0].strip()
        if (match.group()).find('WRITE') != -1:
            s_list = ((match.group()).split('{', 1)[1].strip()).split(' ', 5)
            del s_list[4:]
            if metric_type == 'Storage':
                D_WR_STATS[uuid] = s_list
            else:
                V_WR_STATS[uuid] = s_list
        elif (match.group()).find('READ') != -1:
            s_list = ((match.group()).split('{', 1)[1].strip()).split(' ', 5)
            del s_list[4:]
            if metric_type == 'Storage':
                D_RD_STATS[uuid] = s_list
            else:
                V_RD_STATS[uuid] = s_list
        return True
    return False

def process_get_stats(api, p_list):
    """Processes the GetStats and GetVolumeStats API call

       Parameters:
            api - the api being processed
            p_list - the parameter list
    """
    # Typically don't want to see all the individual calls
    # to GetStats in the script output.
    cmd = ''
    if api == 'GetStats':
        if p_list[1] == 'true':
            cmd += ' --read'
        else:
            cmd += ' --write'
        if p_list[0] == 'true':
            cmd += ' --device-uuid ' + p_list[3]
        else:
            cmd += ' --volume-uuid ' + v_sub(p_list[3])
        if p_list[2] == 'true':
            cmd += ' --clear '
    else: # GetVolumeStats or GetVolumeManifest
        print "api: {} p_list: {}".format(api, p_list)
        cmd += ' --volume-uuid ' + v_sub(p_list[1])
        if p_list[0] == 'true':
            cmd += ' --clear '
    return cmd

def process_arg_value(arg, val):
    """Processes an individual argument and value
       Parameters:
            arg - the argument being processed
            val - the parameter value
    """
    cmd = arg
    if re.search(r"--vol.*-(uuid|series)", arg):
        cmd += v_sub(val)
    elif re.search(r"--(pit|pit-uuid)", arg):
        cmd += pit_sub(val)
    elif arg == ' --device ':
        cmd += d_sub(val)
    else:
        cmd += val
    return cmd

def process_export_lun(api, p_list):
    """Processes the ExportLun and UnExportLun API call

       Parameters:
            api - the api being processed
            p_list - the parameter list
    """
    cmd = ''
    api_def = API_DICT[api]
    is_pit = False
    idx = 0
    for arg in api_def['params']:
        if arg == ' --vol-series ':
            v_idx = idx
            cmd += arg + v_sub(p_list[v_idx])
        elif arg == ' --pit ':
            if p_list[idx]:
                is_pit = True
                cmd += arg + pit_sub(p_list[idx])
        elif arg == ' --export-name ':
            cmd += arg + e_name(p_list[v_idx], p_list[idx])
        elif arg == ' --readonly ':
            if p_list[idx] == 'false':
                cmd += arg
        idx += 1
    if not is_pit:
        cmd += add_mount_cmd(api, p_list[v_idx])
    return cmd


def process_api_params(api, p_list):
    """Processes the API call parameters

       Parameters:
            api - the api being processed
            p_list - the parameter list
    """
    cmd = ''
    api_def = API_DICT[api]
    if (api == 'ExportLun' or api == 'UnexportLun'):
        cmd += process_export_lun(api, p_list)
    else:
        for arg in api_def['params']:
            idx = api_def['params'].index(arg)
            if arg == ' --readonly ' and p_list[idx] == 'false':
                cmd += arg
            elif idx < len(p_list) and p_list[idx]:
                cmd += process_arg_value(arg, p_list[idx])

    return cmd

def process_error_msg(line_str):
    """Gets the error message from the log line
    """
    print "line: {}".format(line_str)
    msg = ''
    regex = r"(failed:|error:).*"
    match = re.search(regex, line_str)
    if match:
        msg = (match.group()).split(":", 1)[1]
    else:
        #nuvoapi.apiError
        regex = r"(What:).*"
        match = re.search(regex, line_str)
        if match:
            msg = (match.group()).split(",", 2)[1]
    return msg

def process_nuvo_api_command(line_str):
    """A NUVOAPI entry in agentd.log is generally in the following form:
       NUVOAPI ApiName(param1, param2, ...)
       This compares each line for an NUVOAPI command entry.
       If it matches, looks up the command line syntax in the API_DICT
       Constructs a nuvo_vm command line with parameters substituted.
       Optional entries are indicated by omission in the agentd.log entry.

       Parameters:
            line_str - line string
    """
    for api in API_DICT:
        api_str = "NUVOAPI " + api
        regex = re.escape(api_str) + r".((?!volumeSeriesRequestState).)*$"
        match = re.search(regex, line_str)
        if match:
            cmd = '\t'
            match_str = (match.group()).split(api)[1]
            status = cmd_status(match_str, api)
            if status == '':
                p_list = get_params(match_str)
                api_def = API_DICT[api]
                cmd += NUVO_CMD + api_def['cmd']
                if api == 'GetStats' or api == 'GetVolumeStats' or api == 'GetVolumeManifest':
                    regex = r"(for.(read|write))"
                    if not SUPPRESS_STATS and not re.search(regex, line_str):
                        cmd += process_get_stats(api, p_list)
                    else:
                        return
                else:
                    cmd += process_api_params(api, p_list)
            elif status == 'failed':
                if api == 'GetStats':
                    return
                msg = process_error_msg(line_str)
                last_cmd = COMMANDS.pop()
                cmd += "# Agentd reported the next NUVO API call failed. Message: " \
                    + msg + "\n" + last_cmd
            elif status == 'succeeded':
                if api == 'UseCacheDevice':
                    # On success UseCacheDevice returns the amount of cache capacity added.
                    regex = r"usableSizeBytes:.*\ "
                    match = re.search(regex, line_str)
                    if match:
                        match_str = (match.group()).split('usableSizeBytes:')[1].strip()
                        last_cmd = COMMANDS.pop()
                        cmd += "# Cache device usable size: " + match_str + "\n" + last_cmd

            COMMANDS.append(cmd)
            return

if __name__ == '__main__':
    main()
