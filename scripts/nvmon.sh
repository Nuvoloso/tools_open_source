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

# Uses apple script to talk to an iTerm2 and launch a number of nvctl commands to follow objects.
# Launch from an iTerm2 window.
# Ref: https://www.iterm2.com/documentation-scripting.html

# iTerm2 profile to use
PROFILE="same profile"

# The NVMON_CMDS environment string can provide a space separated list of objects
# that support "nvctl OBJ list -f". Additionally, "watch" can be specified for events.
declare -a CMDS
CMDS=(${NVMON_CMDS:-volume-series-requests volume-series clusters nodes storage watch})

NVCTL=$(which nvctl)
if [[ $? != 0 ]]; then
    echo "nvctl not found on path"
    exit 1
fi

CMDF=/tmp/cmd$$
trap "rm -f $CMDF" EXIT

cat <<ENDOFCMD >$CMDF
#!/bin/bash
printf "\\e];%s\\a" \$1
clear
if [[ \$1 != "watch" ]] ; then
    $NVCTL \$1 list -f 
else
    $NVCTL \$1 
fi
ENDOFCMD

# (reverse (cdr $CMDS)) and encapsulate with quotes and commas
unset RCMDS; for x in ${CMDS[*]:1}; do RCMDS="\"$x\", $RCMDS"; done
SPLITCMDS=${RCMDS/%, /}

osascript <<ENDOFSCRIPT
tell application "iTerm2"
 tell current session of current window
   set columns to 237
   set commands to { $SPLITCMDS }
   repeat with cmd in commands
      split horizontally with $PROFILE command "bash $CMDF " & cmd
   end repeat
 end tell
end tell
ENDOFSCRIPT
bash $CMDF ${CMDS[0]}
