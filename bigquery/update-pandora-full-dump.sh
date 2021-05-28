#! /bin/bash

if [ -z "$*" ];
  then printf "
Backend Table [-t]
Entity ID [-i]
  ";
  exit 1;
fi

while getopts t:i:f: option
do
 case "${option}"
 in
 t) TABLE=${OPTARG};;
 i) ENTITY_ID=${OPTARG};;
 *) exit 1;;
 esac
done

ENTITY_ID_LOWER=$(echo "$ENTITY_ID" | tr '[:upper:]' '[:lower:]')
PROJECT='fulfillment-dwh-production'

DEST_DATASET='pandata_raw_pandora'
DEST_TABLE_PREFIX="backend__production__${TABLE}__full__"
DEST_TABLE="${DEST_TABLE_PREFIX}${ENTITY_ID_LOWER}"

DEST_TABLE_INC_PREFIX="backend__production__${TABLE}__inc__"
DEST_TABLE_INC="${DEST_TABLE_INC_PREFIX}mjm_at"  # use mjm_at for reference

SOURCE_TABLE_PREFIX="ml_be_"
SOURCE_TABLE="${SOURCE_TABLE_PREFIX}${TABLE}"
SOURCE_DATASET="dl_pandora"


echo "Using schema from fulfillment-dwh-production:${DEST_DATASET}.${DEST_TABLE_INC}..."
echo ""

RDBMS_ID=$(bq query -n 1 --format=json --use_legacy_sql=false "SELECT pd_rdbms_id FROM fulfillment-dwh-production.pandata_curated.pd_entities WHERE global_entity_id = '${ENTITY_ID}'" | jq '.[0]["pd_rdbms_id"]' | tr -d '"')
SCHEMA_FIELDS=$(bq show --format=json "fulfillment-dwh-production:${DEST_DATASET}.${DEST_TABLE_INC}" | jq '.schema.fields[].name' | tr -d '"')

SCHEMA_FIELDS=$(echo "${SCHEMA_FIELDS}" | tr '\n' ',')
SCHEMA_FIELDS=${SCHEMA_FIELDS//exec_date_utc,/}  # remove exec_date_utc that is only added in incremental tables

echo "Project ID: ${PROJECT}"
echo "Source table: ${SOURCE_DATASET}.${SOURCE_TABLE}"
echo "Destination table: ${DEST_DATASET}.${DEST_TABLE}"
echo ""

if [[ "${TABLE}" =~ ^(menus|schedules|specialdays)$ ]]; then
  SCHEMA_FIELDS=${SCHEMA_FIELDS/start_hour/'TIMESTAMP("1970-01-01 " || IF(start_hour = "24:00:00", "00:00:00", start_hour)) AS start_hour'}
  SCHEMA_FIELDS=${SCHEMA_FIELDS/stop_hour/'TIMESTAMP("1970-01-01 " || IF(stop_hour = "24:00:00", "23:59:59", stop_hour)) AS stop_hour'}
elif [[ "${TABLE}" -eq "discount_schedule" ]]; then
  SCHEMA_FIELDS=${SCHEMA_FIELDS/start_time/'TIMESTAMP("1970-01-01 " || IF(start_time = "24:00:00", "00:00:00", start_time)) AS start_time'}
  SCHEMA_FIELDS=${SCHEMA_FIELDS/end_time/'TIMESTAMP("1970-01-01 " || IF(end_time = "24:00:00", "00:00:00", end_time)) AS end_time'}
fi

QUERY="SELECT
${SCHEMA_FIELDS}
FROM ${PROJECT}.${SOURCE_DATASET}.${SOURCE_TABLE}
WHERE rdbms_id = ${RDBMS_ID}
  AND global_entity_id = '${ENTITY_ID}'"

echo "$QUERY"

printf "\nRunning query..."

bq query \
  -n 0 \
  --batch \
  --use_legacy_sql=false \
  --destination_table "${PROJECT}:${DEST_DATASET}.${DEST_TABLE}" \
  -sync=false \
  "${QUERY}"
