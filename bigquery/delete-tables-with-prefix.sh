#! /bin/bash

if [ -z "$*" ];
  then printf "
Project ID     [-p]
Dataset ID     [-d]
Table prefix   [-t]
  ";
  exit 1;
fi

while getopts p:d:t: option
do
 case "${option}"
 in
 p) PROJECT=${OPTARG};;
 d) DATASET=${OPTARG};;
 t) TABLE=${OPTARG};;
 *) exit 1;;
 esac
done

# TABLES=($(bq ls --max_results=10000000  "${PROJECT}:${DATASET}." | grep TABLE | grep "${TABLE}" | awk '{print $1}'))
TABLES=($(bq ls --max_results=10000000  --project_id="${PROJECT}" --dataset_id="${DATASET}" |
  grep TABLE |
  grep "${TABLE}" |
  awk '{print $1}'
))

FULL_TABLES=$(printf "${DATASET}.%s\n" "${TABLES[@]}")

echo "Removing ..."
echo "${FULL_TABLES}"

echo "${FULL_TABLES}" | xargs -n1 bq rm -f --project_id="${PROJECT}"
