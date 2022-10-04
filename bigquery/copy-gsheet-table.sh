#! /bin/zsh

# Copies a google sheet external table

if [ -z "$*" ];
  then printf "
Source Fully Qualified table ID          [-s]
Destination Fully Qualified table ID     [-d]
  ";
  exit 1;
fi

while getopts s:d: option
do
 case "${option}"
 in
 s) SOURCE_TABLE_ID=${OPTARG};;
 d) DESTINATION_TABLE_ID=${OPTARG};;
 *) exit 1;;
 esac
done


TABLE_METADATA=$(bq show --format=json "${SOURCE_TABLE_ID}" )
echo $TABLE_METADATA

SCHEMA=$(echo ${TABLE_METADATA} | jq ".schema.fields" --raw-output)
EXTERNAL_DATA_CONFIGURATION=$(echo ${TABLE_METADATA} | jq ".externalDataConfiguration" --raw-output)
SOURCE_URI=$(echo ${TABLE_METADATA} | jq ".externalDataConfiguration.sourceUris[0]" --raw-output)

echo $SCHEMA > $TMPDIR/temp_schema.json

TABLE_DEF=$(bq mkdef --noautodetect --source_format="GOOGLE_SHEETS" $SOURCE_URI $TMPDIR/temp_schema.json)

echo $TABLE_DEF > $TMPDIR/temp_table_def.json

bq rm -f --table "${DESTINATION_TABLE_ID}"

bq mk \
  --use_legacy_sql=false \
  --external_table_definition \
  "${TMPDIR}/temp_table_def.json" \
  --table \
  "${DESTINATION_TABLE_ID}"
