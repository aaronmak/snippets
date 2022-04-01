#! /bin/zsh

# Copies a view from one dataset to another

if [ -z "$*" ];
  then printf "
Source Fully Qualified View ID          [-s]
Destination Fully Qualified View ID     [-d]
  ";
  exit 1;
fi

while getopts s:d: option
do
 case "${option}"
 in
 s) SOURCE_VIEW_ID=${OPTARG};;
 d) DESTINATION_VIEW_ID=${OPTARG};;
 *) exit 1;;
 esac
done


VIEW_QUERY=$(bq show --format=json "${SOURCE_VIEW_ID}" | jq '.view.query' --raw-output)

echo '---QUERY---'
echo ''
echo ${VIEW_QUERY}
echo ''
echo '----END----'

bq mk \
  --use_legacy_sql=false \
  --view \
  ${VIEW_QUERY} \
  "${DESTINATION_VIEW_ID}"
