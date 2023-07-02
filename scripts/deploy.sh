#!/bin/sh

chmod +x backup.sh
chmod +x run.sh
chmod +x maintain.sh

mv backup.sh /home/ec2-user/backup.sh
mv run.sh /home/ec2-user/run.sh
mv maintain.sh /home/ec2-user/maintain.sh
mv superuser.sh /home/ec2-user/superuser.sh

sudo mv timers/backup.timer /etc/systemd/system/backup.timer
sudo mv timers/backup.service /etc/systemd/system/backup.service

sudo mv supervisor/supervisord.service /etc/systemd/system/supervisord.service
sudo mv supervisor/supervisord.conf /etc/supervisord.conf

sudo yum update
sudo yum install python3-pip
pip3 install supervisor

sudo yum install -y docker
sudo usermod -a -G docker ec2-user
newgrp docker
