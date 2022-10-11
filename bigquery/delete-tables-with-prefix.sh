#! /bin/bash

if [ -z "$*" ];
  then printf "
Project ID     [-p]
Dataset ID     [-d]
Regex search   [-r]
  ";
  exit 1;
fi

while getopts p:d:r: option
do
 case "${option}"
 in
 p) PROJECT=${OPTARG};;
 d) DATASET=${OPTARG};;
 r) PATTERN=${OPTARG};;
 *) exit 1;;
 esac
done

TABLES=($(bq ls --max_results=10000000  --project_id="${PROJECT}" --dataset_id="${DATASET}" |
  awk '{print $1}' |
  grep "${PATTERN}"
))

FULL_TABLES=$(printf "${DATASET}.%s\n" "${TABLES[@]}")

echo "Removing ..."
echo "${FULL_TABLES}"

echo "${FULL_TABLES}" | xargs -n1 bq rm -f --project_id="${PROJECT}"
