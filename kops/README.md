# kops

Utilities to use with [kops](https://github.com/kubernetes/kops).  These assume an AWS client environment.

## Create a management cluster
The [nuvomgmt-cluster.sh](/nuvomgmt-cluster.sh) script illustrates how to create a kops management cluster.

## nuvodev

[nuvodev.py](nuvodev/nuvodev.py) -- wrapper script for kops

[reset_kops_aws_creds.py](nuvodev/reset_kops_aws_creds.py) -- reset credentials in AWS (delete user and group)

### dependencies

Either run in a virtualenv or have all dependencies installed on the system for this script to run

- kubectl

    Mac: `brew install kubernetes-cli`

- AWS CLI

    `# pip install awscli`

- boto3

    `# pip install boto3`

- ruamel

    `pip install 'ruamel.yaml<=0.15'`
