#!/bin/bash

START_DIR=$PWD

mkdir -p /tmp/DeckCompanion/
cd /tmp/DeckCompanion/
git clone https://github.com/z0z0z/mf-install.git
cd mf-install/

./mf-install.sh -proton

cd $START_DIR