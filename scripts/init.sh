#!/bin/sh

cd /home/ec2-user
mkdir spanza-journal-watch
cd spanza-journal-watch
git init
git clone https://github.com/uppertoe/spanza-journal-watch.git
# Will require git credentials
cd /home/ec2-user/spanza-journal-watch
chmod +x deploy.sh
sh deploy.sh
