#!/bin/sh
mv timers/backup.timer /etc/systemd/system/backup.timer
mv timers/backup.service /etc/systemd/system/backup.service
systemctl start backup.timer

chmod +x backup.sh
chmod +x run.sh
mv backup.sh /home/ec2-user/backup.sh
mv run.sh /home/ec2-user/run.sh
