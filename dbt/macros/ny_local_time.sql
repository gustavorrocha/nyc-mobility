{#
  Horários locais de NY (sem fuso, como publicados pela TLC) nas mudanças de horário de verão.
  As transições ocorrem às 02:00 locais, então só as horas 01 e 02 precisam de verificação:
  - inexistente: 02:00–02:59 no início do horário de verão (o relógio pula para 03:00);
  - ambíguo: 01:00–01:59 na volta (a hora ocorre duas vezes, em EDT e depois em EST).
  `utc` é a conversão padrão do PostgreSQL (`local at time zone 'America/New_York'`).
#}

{# true quando o horário local não existe (a conversão de volta não reproduz o original). #}
{% macro ny_is_nonexistent(local, utc) -%}
    (extract(hour from {{ local }}) = 2
        and ({{ utc }} at time zone 'America/New_York') <> {{ local }})
{%- endmacro %}

{# A outra ocorrência (em UTC) de um horário ambíguo; NULL quando o horário não é ambíguo. #}
{% macro ny_ambiguous_alternative(local, utc) -%}
    case when extract(hour from {{ local }}) = 1 then
        case
            when ({{ utc }} - interval '1 hour') at time zone 'America/New_York' = {{ local }}
                then {{ utc }} - interval '1 hour'
            when ({{ utc }} + interval '1 hour') at time zone 'America/New_York' = {{ local }}
                then {{ utc }} + interval '1 hour'
        end
    end
{%- endmacro %}
