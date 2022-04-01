#! /bin/zsh


# get all views in a dataset

if [ -z "$*" ];
  then printf "
Project ID [-p]
Dataset ID [-d]
  ";
  exit 1;
fi

while getopts p:d: option
do
 case "${option}"
 in
 p) PROJECT_ID=${OPTARG};;
 d) DATASET_ID=${OPTARG};;
 *) exit 1;;
 esac
done

bq ls -n 1000000 --project_id "${PROJECT_ID}" --format=json "${DATASET_ID}" | jq -c '.[] | select( .type | contains("VIEW"))' | jq .id
