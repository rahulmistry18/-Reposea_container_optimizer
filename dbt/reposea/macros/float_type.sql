{#
  DuckDB (dev target) and BigQuery (prod target) don't agree on a floating-
  point type name — DuckDB wants DOUBLE, BigQuery wants FLOAT64, and neither
  accepts the other's spelling. This macro picks the right one based on
  which adapter dbt is actually running against, so stg_containers.sql
  doesn't need a different cast list per environment.
#}
{% macro float_type() %}
  {% if target.type == 'bigquery' %}
    {{ return('FLOAT64') }}
  {% else %}
    {{ return('DOUBLE') }}
  {% endif %}
{% endmacro %}
