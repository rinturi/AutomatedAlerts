#!/bin/bash
# =============================================================================
# Banking Mock Stack -- AWS EC2 Free Tier Setup Script
# Ubuntu 22.04 LTS (t2.micro, ap-south-1)
# Run as: bash setup-aws.sh
# =============================================================================

set -e

echo "=========================================="
echo " Banking Mock -- AWS EC2 Setup"
echo "=========================================="

# ── 1. System update ──────────────────────────────────────────────────────────
echo ""
echo "[1/8] Updating system packages..."
sudo apt-get update -y
sudo apt-get upgrade -y

# ── 2. Install Docker ─────────────────────────────────────────────────────────
echo ""
echo "[2/8] Installing Docker..."
sudo apt-get install -y ca-certificates curl gnupg lsb-release

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# ── 3. Docker post-install ────────────────────────────────────────────────────
echo ""
echo "[3/8] Configuring Docker..."
sudo usermod -aG docker ubuntu
sudo systemctl enable docker
sudo systemctl start docker

# ── 4. Add 2GB swap (critical for t2.micro -- prevents OOM kills) ─────────────
echo ""
echo "[4/8] Adding 2GB swap space..."
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo "Swap created:"
  free -h
else
  echo "Swap already exists -- skipping."
  free -h
fi

# ── 5. Install and configure Nginx for L1 Dashboard ──────────────────────────
echo ""
echo "[5/8] Installing Nginx for L1 Dashboard (port 80)..."
sudo apt-get install -y nginx

# Create default site config pointing to dashboard HTML
sudo tee /etc/nginx/sites-available/default > /dev/null << 'NGINX_CONF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    root /var/www/html;
    index index.html;

    server_name _;

    location / {
        try_files $uri $uri/ =404;
    }
}
NGINX_CONF

sudo systemctl enable nginx
sudo systemctl restart nginx
echo "Nginx installed and started on port 80."

# ── 6. Set environment variables ──────────────────────────────────────────────
echo ""
echo "[6/8] Setting up environment variables..."

# Check if already set
if grep -q "OPENAI_API_KEY" ~/.bashrc; then
  echo "OPENAI_API_KEY already in ~/.bashrc -- skipping."
else
  cat >> ~/.bashrc << 'ENVVARS'

# ── Banking Mock -- AI and Integration Keys ────────────────────────────────
export OPENAI_API_KEY=""        # Set this: sk-proj-your-key-here
export JIRA_EMAIL=""            # Set this: your@email.com
export JIRA_API_TOKEN=""        # Set this: token from id.atlassian.com
ENVVARS
  echo "Environment variable placeholders added to ~/.bashrc"
  echo ""
  echo "IMPORTANT: Edit ~/.bashrc and fill in your API keys:"
  echo "  nano ~/.bashrc"
  echo "  # Set OPENAI_API_KEY, JIRA_EMAIL, JIRA_API_TOKEN"
  echo "  source ~/.bashrc"
fi

# ── 7. Clone repository ───────────────────────────────────────────────────────
echo ""
echo "[7/8] Setting up project..."
if [ -d ~/banking-mock ]; then
  echo "~/banking-mock already exists."
  echo "To update: cd ~/banking-mock && git pull"
else
  echo "Cloning repository..."
  git clone https://github.com/rinturi/banking-mock.git ~/banking-mock
  echo "Repository cloned to ~/banking-mock"
fi

# ── 8. Deploy dashboard HTML to Nginx ────────────────────────────────────────
echo ""
echo "[8/8] Deploying L1 Dashboard HTML to Nginx..."
if [ -f ~/banking-mock/dashboard/frontend/index.html ]; then
  sudo cp ~/banking-mock/dashboard/frontend/index.html /var/www/html/index.html
  echo "Dashboard deployed to /var/www/html/index.html"
else
  echo "Dashboard HTML not found -- deploy manually after clone:"
  echo "  sudo cp ~/banking-mock/dashboard/frontend/index.html /var/www/html/index.html"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo " Setup complete!"
echo "=========================================="
echo ""
echo " NEXT STEPS:"
echo ""
echo " 1. Log out and back in (required for Docker group):"
echo "      exit"
echo "      ssh banking-vm"
echo ""
echo " 2. Set your API keys in ~/.bashrc:"
echo "      nano ~/.bashrc"
echo "      # Fill in: OPENAI_API_KEY, JIRA_EMAIL, JIRA_API_TOKEN"
echo "      source ~/.bashrc"
echo ""
echo " 3. Start the full stack (16 containers):"
echo "      cd ~/banking-mock"
echo "      OPENAI_API_KEY=\$OPENAI_API_KEY \\"
echo "      JIRA_EMAIL=\$JIRA_EMAIL \\"
echo "      JIRA_API_TOKEN=\$JIRA_API_TOKEN \\"
echo "        docker compose -f docker-compose.free-tier.yml up -d --build"
echo ""
echo " 4. Verify everything is running (after ~60 seconds):"
echo "      docker compose -f docker-compose.free-tier.yml ps"
echo "      curl -s http://localhost:8080/incidents | python3 -m json.tool"
echo "      curl -s http://localhost:9005/health    | python3 -m json.tool"
echo ""
echo " IMPORTANT: Open these ports in your EC2 Security Group:"
echo "   Port 80   -- L1 Dashboard (0.0.0.0/0)"
echo "   Port 8080 -- Dashboard API (0.0.0.0/0)"
echo "   Port 3000 -- Grafana (your-ip/32)"
echo "   Port 9090 -- Prometheus (your-ip/32)"
echo "   Port 22   -- SSH (your-ip/32)"
echo ""
echo " Service URLs (replace EC2_IP with your Elastic IP):"
echo "   L1 Dashboard  -> http://EC2_IP"
echo "   Dashboard API -> http://EC2_IP:8080/incidents"
echo "   Grafana       -> http://EC2_IP:3000  (admin / bankadmin123)"
echo "   Prometheus    -> http://EC2_IP:9090"
echo "   payment-svc   -> http://EC2_IP:8001/docs"
echo "   auth-svc      -> http://EC2_IP:8002/docs"
echo "   loan-svc      -> http://EC2_IP:8003/docs"
echo "   notification  -> http://EC2_IP:8004/docs"
echo ""
echo " Useful commands:"
echo "   docker compose -f docker-compose.free-tier.yml logs -f agents"
echo "   docker compose -f docker-compose.free-tier.yml ps"
echo "   docker stats --no-stream"
echo "   curl http://localhost:9005/tools/list_cached_resolutions | python3 -m json.tool"
