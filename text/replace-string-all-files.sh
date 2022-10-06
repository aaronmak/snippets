#! /bin/bash

if [ -z "$*" ];
  then printf "
Old String [-r]
New String [-s]
Directory to search [-d]
  ";
  exit 1;
fi

while getopts r:s:d: option
do
 case "${option}"
 in
 r) OLD_STRING=${OPTARG};;
 s) NEW_STRING=${OPTARG};;
 d) DIR=${OPTARG};;
 *) exit 1;;
 esac
done

echo  "s/${OLD_STRING}/${NEW_STRING}/"

rg "${OLD_STRING}" "${DIR}" -l | xargs sed -i '' "s/${OLD_STRING}/${NEW_STRING}/"
