sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap  /swapfile
sudo swapon /swapfile

# Append to /etc/fstab to survive reboot
line="/swapfile none swap sw 0 0"
echo "$line" | sudo tee -a /etc/fstab > /dev/null

# Set overcommit to 1 for Redis
echo 'vm.overcommit_memory = 1' | sudo tee -a /etc/sysctl.conf


swapon  --show
free -h
