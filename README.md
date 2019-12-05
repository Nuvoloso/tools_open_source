# tools
Internal tools used for development, test, etc.

## `certs` (the certificates folder)

Our software uses shared certificates for securing communication in our system.  This repository is used to generate the set of required certificates for our services.

Before generating the certificates, you must set the environment variable CA_PASSWORD which holds our certificate authority password.  You can find the password definition in the google doc

https://docs.google.com/document/d/14bqcXv8U7nE1ItIfmsOvcYRFiFEop0ueTKDOC8RguoM/edit#heading=h.sl3wdfkvg07e

You should need to regenerate the certificates infrequently.

Other repositories will reference this repository in order to get a copy of the certificates.  You will need to review the Makefile in each repo to determine what variables need to be set to properly acquire these files.  You probably need to validate the `TOOLS_REPO` variable and how it is used in each repo.

The alternatives are to either copy a generated `certs` folder to your other repository, or download the certs folder from the artifacts of the `tools` Jenkins job to your other repository.

## `gobin`

Archived [go 1.13.x](https://golang.org/) build and development tools, specifically those used in the [kontroller](https://github.com/Nuvoloso/kontroller) repo.
There is a sub-directory per platform. Note that for (Debian/Ubuntu) Linux, several tools are installed from packages and are not archived.

## `kops`

The [kops](kops) folder contains utilities for use with [kops](https://github.com/kubernetes/kops) to get a Kubernetes cluster up and running.

## `pv-test`

The [pv-test](pv-test) folder contains various scripts and YAML files that can be used within a Kubernetes cluster to
demonstrate and test the usage a Nuvoloso volume to back a Kubernetes Persistent Volume claim.
They can-

+ Create a Nuvoloso volume, either Dynamically (CSI) or Statically (CSI, Flex)
+ Declare a Kubernetes persistent volume that refers to the Nuvoloso volume
+ Create a Kubernetes deployment that uses the persistent volume via a claim (and mounting the volume)
+ [TBD] Delete the deployment, releasing the claim (and unmounting the volume)

## `scripts`

The [scripts](scripts) folder contains various scripts to debug and manage your Kubernetes cluster and related
service-provider resources.

## `wp-test`

The [wp-test](wp-test) folder contains scripts and YAML files that can be used within a Kubernetes cluster to demonstrate
the deployment of a WordPress application that is backed by Nuvoloso volumes. It-

- creates 2 deployments (WordPress, MySql) each with their own volume
- creates the volumes dynamically using the CSI volume driver
- has options to specify consistency grouping
