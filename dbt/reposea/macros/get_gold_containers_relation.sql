{#
  In `dev` (DuckDB) there's no live warehouse to point at, so staging reads
  the committed seed snapshot. In `prod` (BigQuery) it reads the real table
  that warehouse/bigquery_load.py loads every run. One staging model, two
  environments — nothing downstream (marts, tests, docs) needs to know which.
#}
{% macro get_gold_containers_relation() %}
  {% if target.name == 'prod' %}
    {{ return(source('reposea_gold', 'containers')) }}
  {% else %}
    {{ return(ref('gold_containers_seed')) }}
  {% endif %}
{% endmacro %}
