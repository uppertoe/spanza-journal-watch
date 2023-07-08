sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap  /swapfile
sudo swapon /swapfile

# Append to /etc/fstab to survive reboot
line="/swapfile none swap sw 0 0"
echo "$line" | sudo tee -a /etc/fstab > /dev/null

swapon  --show
free -h
