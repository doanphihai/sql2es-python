
# Context

This project help convert sql to elasticsearch/opensearch query syntax
This prefer opensearch syntax over elasticsearch

# References
- https://www.elastic.co/docs/reference/
- https://docs.opensearch.org/latest/query-dsl

# Requirements

Inputs:
- url: opensearch/elastic url
- dryrun: true/false
Outputs:

with dryrun: payload in json only
without dryrun: send to elasticsearch

Writen in rust
