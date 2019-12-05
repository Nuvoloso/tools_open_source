#!/bin/bash

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

# Script to monitor and download the log of a particular container in a PODPAT when a crash is observed.

CONTAINER=nuvo
LOGDIR=/tmp
NAMESPACE=nuvoloso-cluster
PODPAT="nuvoloso-node"

while [ $# -gt 0 ]; do
	case $1 in
	(-c) CONTAINER=$2; shift;;
	(-d) LOGDIR=$2; shift;;
	(-n) NAMESPACE=$2; shift;;
	(-m) NAMESPACE="nuvoloso-management";;
	(-p) PODPAT=$2; shift;;
	(*) echo "Unknown argument: $1"; exit 1;;
	esac
	shift
done
kcc="kubectl -n $NAMESPACE"

RESTART_NUM=
while (( 1 )) ; do
	printf "\r[$NAMESPACE] monitoring /$PODPAT/.$CONTAINER @ "; echo -n $(date)
	$kcc get pods 2>/dev/null | grep $PODPAT | while read X; do
		set -- $X
    	if [[ $3 =~ (Error|Crash) || ($RESTART_NUM != "" && $4 -ne $RESTART_NUM) ]]; then
			F="$LOGDIR/$1-$4.log"
			FP="$LOGDIR/$1-$4p.log"
			if [[ ! ( -f $F  || -f $FP ) ]]; then
				# kubectl does not provide any indication of failure externally so current and previous
				printf "\n$X\n"
				echo "Fetching [$NAMESPACE]$1.$CONTAINER logs"
				$kcc logs $1 -c $CONTAINER -p >$FP
				if [[ $? != 0 ]]; then
					echo "Failed to download previous log to $FP"
					rm $FP
				else
					echo "Downloaded previous log to $FP"
				fi
				$kcc logs $1 -c $CONTAINER >$F
				if [[ $? != 0 ]]; then
					echo "Failed to download log to $F"
					rm $F
				else
					echo "Downloaded log to $F"
				fi
			fi
		fi
		RESTART_NUM=$4
	done
	sleep 1
done
