# EFK (Elasticsearch, Fluentd, Kibana)
Logging stack used to store and view all of kubernetes logs in a cluster

## How to use the files in this repo
- Execute the deploy_logging.sh
- This will create the logging namespace and deploy the EFK stack inside of it.
- May take a few minutes for the Elasticsearch pods to be created.
- The script will also expose the kibana interface on port 5601 

## Configuring Kibana
- Once Kibana is up and running, click the discover tab on the left hand side
- It will ask you to create a pattern, specify "logstash-*" as the pattern. Click next.
- In step 2, set the time filter field to "@timestamp"
