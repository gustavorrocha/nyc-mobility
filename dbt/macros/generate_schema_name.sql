{# Usa o schema configurado (core/analytics) como está, sem o prefixo padrão do dbt. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}
