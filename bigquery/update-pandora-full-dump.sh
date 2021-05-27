#! /bin/bash

if [ -z "$*" ];
  then printf "
Backend Table [-t]
Entity ID [-i]
  ";
  exit 1;
fi

while getopts t:f:e:u:i: option
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

SOURCE_TABLE_PREFIX="ml_be_"
SOURCE_TABLE="${SOURCE_TABLE_PREFIX}${TABLE}"
SOURCE_DATASET="dl_pandora"


echo "Project ID: ${PROJECT}"
echo "Source table: ${SOURCE_DATASET}.${SOURCE_TABLE}"
echo "Destination table: ${DEST_DATASET}.${DEST_TABLE}"
echo ""

QUERY="SELECT * EXCEPT (
rdbms_id,
dwh_last_modified,
dwh_row_hash,
timezone,
global_entity_id,
created_date,
merge_layer_run_from,
merge_layer_created_at,
merge_layer_updated_at
)
FROM ${PROJECT}.${SOURCE_DATASET}.${SOURCE_TABLE}
WHERE global_entity_id = '${ENTITY_ID}'"

echo "$QUERY"

printf "\nRunning query..."

bq query \
  -n 0 \
  --batch \
  --use_legacy_sql=false \
  --destination_table "${PROJECT}:${DEST_DATASET}.${DEST_TABLE}" \
  "${QUERY}"
