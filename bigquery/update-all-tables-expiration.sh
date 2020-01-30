#! /bin/bash

if [ -z "$*" ];
  then printf "
Project ID     [-p]
Dataset ID     [-d]
Expiry in days [-t]
  ";
  exit 1;
fi

while getopts p:d:t: option
do
 case "${option}"
 in
 p) PROJECT=${OPTARG};;
 d) DATASET=${OPTARG};;
 t) DAYS=${OPTARG};;
 *) exit 1;;
 esac
done

SECONDS_EXPIRY=$((DAYS * 60 * 60 * 24))
TABLES=($(bq ls -n 100000000 "${PROJECT}:${DATASET}."| awk '{print $1}' | tail +3))
FULL_TABLES=$(printf "${DATASET}.%s\n" "${TABLES[@]}")

echo "Seconds to expiry: ${SECONDS_EXPIRY}"
echo "Updating tables in ${PROJECT}.${DATASET}..."
echo "${FULL_TABLES}" | xargs -n1 bq update --expiration ${SECONDS_EXPIRY}
