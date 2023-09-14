echo "Stop supervisor"
sudo systemctl stop supervisord
echo "Take docker-compose offline"
docker-compose -f spanza-journal-watch/production.yml down
sudo systemctl stop docker.service
sudo systemctl disable docker.service
sudo rm /usr/local/bin/docker-compose

sudo curl -L https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m) -o /usr/local/bin/docker-compose
export PATH=$PATH:/usr/local/bin
sudo chmod +x /usr/local/bin/docker-compose
sudo systemctl enable docker.service
sudo systemctl start docker.service
