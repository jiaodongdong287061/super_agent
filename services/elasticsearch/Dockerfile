FROM docker.elastic.co/elasticsearch/elasticsearch:8.12.2

RUN elasticsearch-plugin install --batch https://release.infinilabs.com/plugin/elasticsearch-analysis-ik-8.12.2.zip

ENV discovery.type=single-node
ENV xpack.security.enabled=false
ENV ES_JAVA_OPTS="-Xms4g -Xmx4g"
