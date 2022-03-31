#! /bin/zsh

if [ -z "$*" ];
  then printf "
Project ID     [-p]
Dataset ID     [-d]
Table ID       [-t]
Days to revert [-f]
  ";
  exit 1;
fi

while getopts p:d:t:f: option
do
 case "${option}"
 in
 p) PROJECT=${OPTARG};;
 d) DATASET=${OPTARG};;
 t) TABLE=${OPTARG};;
 f) DAYS=${OPTARG};;
 *) exit 1;;
 esac
done

echo "Table to revert: ${TABLE}"
echo "Days to revert: ${DAYS}"

NOW=$(date '+%s')
REVERT_SECONDS=$(date -d "${DAYS} days ago" +%s)
REVERT_MILLIS=$((REVERT_SECONDS * 1000))

echo $REVERT_MILLIS

CURRENT_PROJECT=$(gcloud config get-value core/project -q)

echo "Current timestamp: ${NOW}"
printf "Current GCP Project: %s \n\n" "${CURRENT_PROJECT}"

echo "Reverting ${PROJECT}:${DATASET}.${TABLE} to ${DATASET}.${TABLE}@${REVERT_MILLIS}..."

bq cp "${PROJECT}:${DATASET}.${TABLE}@${REVERT_MILLIS}" "${PROJECT}:${DATASET}.${TABLE}"
