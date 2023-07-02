#!/bin/sh

cd /home/ec2-user
# Install git
sudo yum install -y git
git clone https://github.com/uppertoe/spanza-journal-watch.git
# Will require git credentials
cd /home/ec2-user/spanza-journal-watch/scripts
chmod +x deploy.sh
sh deploy.sh
