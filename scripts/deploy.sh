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

sudo curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m) -o /usr/local/bin/docker-compose
export PATH=$PATH:/usr/local/bin
sudo chmod +x /usr/local/bin/docker-compose
sudo systemctl enable docker.service
sudo systemctl start docker.service

sudo systemctl daemon-reload
sudo systemctl enable --now backup.timer
sudo systemctl enable --now supervisord
