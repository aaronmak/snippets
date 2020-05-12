#! /bin/bash

if [ -z "$*" ];
  then printf "
Old String [-r]
New String [-s]
  ";
  exit 1;
fi

while getopts r:s: option
do
 case "${option}"
 in
 r) OLD_STRING=${OPTARG};;
 s) NEW_STRING=${OPTARG};;
 *) exit 1;;
 esac
done

echo  "s/${OLD_STRING}/${NEW_STRING}/"

rg "${OLD_STRING}" -l | xargs sed -i '' "s/${OLD_STRING}/${NEW_STRING}/"
