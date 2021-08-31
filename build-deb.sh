#!/bin/bash
SMBUS2_TAG="0.4.1"
CURR_PWD=$PWD
git submodule update --recursive
git submodule update --init
cd smbus2-repo/;git checkout $SMBUS2_TAG;cd $CURR_PWD
cp -af smbus2-repo/smbus2/* dentos-poe-agent/opt/poeagent/lib/smbus2 
dpkg-deb -b dentos-poe-agent
