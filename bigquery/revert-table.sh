#! /bin/bash

if [ -z "$*" ];
  then printf "
Dataset ID     [-d]
Table ID       [-t]
Days to revert [-f]
  ";
  exit 1;
fi

while getopts d:t:f: option
do
 case "${option}"
 in
 d) DATASET=${OPTARG};;
 t) TABLE=${OPTARG};;
 f) DAYS=${OPTARG};;
 *) exit 1;;
 esac
done

echo "Table to revert: ${TABLE}"
echo "Days to revert: ${DAYS}"

NOW=$(date '+%s')
REVERT_SECONDS=$(date -j -f %s -v-"${DAYS}"d "${NOW}" +%s)
REVERT_MILLIS=$((REVERT_SECONDS * 1000))

CURRENT_PROJECT=$(gcloud config get-value core/project -q)

echo "Current timestamp: ${NOW}"
printf "Current GCP Project: %s \n\n" "${CURRENT_PROJECT}"

echo "Reverting ${DATASET}.${TABLE} to ${DATASET}.${TABLE}@${REVERT_MILLIS}..."

bq cp "${DATASET}.${TABLE}@${REVERT_MILLIS}" "${DATASET}.${TABLE}"
