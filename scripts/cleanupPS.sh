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
RUN=
while [ $# -gt 0 ]; do
    case $1 in
    (-n) RUN=echo;;
    (*) echo "Unknown flag $1"; exit 1;;
    esac
    shift
done

aws s3 ls | grep "nuvoloso\." | cut -f 3 -d ' ' | while read B; do
	$RUN aws s3 rm s3://$B --recursive
	$RUN aws s3 rb s3://$B
done
