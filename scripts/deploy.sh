#!/bin/sh

chmod +x backup.sh
chmod +x run.sh
chmod +x maintain.sh

mv backup.sh /home/ec2-user/backup.sh
mv run.sh /home/ec2-user/run.sh
mv maintain.sh /home/ec2-user/maintain.sh

sh /home/ec2-user/run.sh

mv timers/backup.timer /etc/systemd/system/backup.timer
mv timers/backup.service /etc/systemd/system/backup.service

pip install supervisor
mv supervisor/supervisord.service /etc/systemd/system/supervisord.service
mv supervisor/supervisord.conf /etc/supervisord.conf

sudo systemctl daemon-reload
sudo systemctl enable --now backup.timer
sudo systemctl enable --now supervisord
