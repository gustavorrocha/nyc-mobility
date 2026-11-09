{#
  Competências a (re)processar por um modelo incremental por `period` (delete+insert).
  `version_column`: coluna de period_versions com a versão dos insumos do modelo; o modelo grava
  essa versão em `source_version`. Carga inicial ou --full-refresh: todas as competências.
#}
{% macro incremental_periods(version_column) %}
    select v.period, v.{{ version_column }} as source_version
    from {{ ref('period_versions') }} as v
    where v.{{ version_column }} is not null
    {% if is_incremental() %}
        and not exists (
            select 1 from {{ this }} as t
            where t.period = v.period and t.source_version = v.{{ version_column }}
        )
    {% endif %}
{% endmacro %}
