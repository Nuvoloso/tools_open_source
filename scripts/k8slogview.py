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
Usage: k8slogview.py [files...]

See the README.md for more information.
"""

import codecs
import fileinput
import json
import sys

if __name__ == '__main__':
    # Force UTF-8 encoded output, needed at least for a pipe
    sys.stdout = codecs.getwriter('UTF-8')(sys.stdout)
    for line in fileinput.input():
        obj = json.loads(line)
        try:
            print obj['log'].rstrip("\n\r")
        except IOError:  # eg when stdout is a pipe that closes
            sys.exit(0)
