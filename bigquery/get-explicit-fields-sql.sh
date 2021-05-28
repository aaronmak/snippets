#! /bin/bash

# returns all fields of a table new line and comma delimited
# useful for removing `SELECT *`

if [ -z "$*" ];
  then printf "
Fully qualified table [-t]
  ";
  # project_id:dataset_id.table_id or dataset_id.table_id
  exit 1;
fi

while getopts t: option
do
 case "${option}"
 in
 t) TABLE=${OPTARG};;
 *) exit 1;;
 esac
done

bq show --format=json "${TABLE}" | jq '.schema.fields[].name' | tr -d '"' | awk '{print $1","}'
