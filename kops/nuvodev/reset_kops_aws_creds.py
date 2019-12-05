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
Script to remove user/group/credentials from AWS
"""

import ConfigParser
from os.path import expanduser
import boto3


def get_key_info(user_name):
    """Get Key information
    """

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

    print "Credentials found in AWS config"
    # mimic the dict returned by the AWS SDK when creating credentials
    keyinfo = {}
    keyinfo['AccessKey'] = {}
    keyinfo['AccessKey']['AccessKeyId'] = key_id
    keyinfo['AccessKey']['SecretAccessKey'] = key_value
    return keyinfo


def main():
    """Main program
    """

    user_name = "kops"
    group_name = "kops"
    keyinfo = get_key_info(user_name)
    print keyinfo

    key_id = keyinfo['AccessKey']['AccessKeyId']

    client = boto3.client('iam')
    client.delete_access_key(
        UserName=user_name,
        AccessKeyId=key_id
    )

    client.remove_user_from_group(
        GroupName=group_name,
        UserName=user_name
    )

    client.delete_user(
        UserName=user_name
    )

    client.detach_group_policy(
        PolicyArn="arn:aws:iam::aws:policy/AmazonEC2FullAccess",
        GroupName=group_name
    )
    client.detach_group_policy(
        PolicyArn="arn:aws:iam::aws:policy/AmazonRoute53FullAccess",
        GroupName=group_name
    )
    client.detach_group_policy(
        PolicyArn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
        GroupName=group_name
    )
    client.detach_group_policy(
        PolicyArn="arn:aws:iam::aws:policy/IAMFullAccess",
        GroupName=group_name
    )
    client.detach_group_policy(
        PolicyArn="arn:aws:iam::aws:policy/AmazonVPCFullAccess",
        GroupName=group_name
    )

    client.delete_group(
        GroupName=group_name
    )


# launch the program
if __name__ == '__main__':
    main()
