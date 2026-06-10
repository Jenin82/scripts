#!/bin/bash

set -e

COLOR_GREEN='\033[0;32m'
COLOR_RED='\033[0;31m'
NO_COLOR='\033[0m'

if ! command -v docker &> /dev/null
then
    echo -e "${COLOR_RED}Docker not found. Please install Docker before running this script.${NO_COLOR}"
    exit 1
fi

read -p "Do you want to authenticate with GitHub container registry? [y/N]: " AUTHENTICATE

if [ "$AUTHENTICATE" == "y" ]; then
    read -p "Enter the GitHub username: " GITHUB_USERNAME
    read -sp "Enter the GitHub personal access token (read:packages): " GITHUB_TOKEN
    echo ""

    echo "$GITHUB_TOKEN" | docker login -u "$GITHUB_USERNAME" --password-stdin ghcr.io

    echo -e "${COLOR_GREEN}Authenticated with GitHub container registry.${NO_COLOR}"
fi

read -p "Do you want to setup deployment scripts? [y/N]: " SETUP_DEPLOYMENT

if [ "$SETUP_DEPLOYMENT" == "y" ]; then
    # Create deployment scripts_dir
    echo "Linking deployment script..."

    SCRIPT_FILE=$(readlink -f -- "${BASH_SOURCE[0]}")
    SCRIPT_DIR=$(dirname "${SCRIPT_FILE}")

    # save pwd in /opt/project_folder
    dirname "${SCRIPT_DIR}" | sudo tee /opt/project_folder

    chmod +x "$SCRIPT_DIR/deploy.sh"
    chmod +x "$SCRIPT_DIR/shell.sh"
    sudo ln -s "$SCRIPT_DIR/deploy.sh" /bin/deploy || echo "Deployment script already linked"
    sudo ln -s "$SCRIPT_DIR/shell.sh" /bin/shell || echo "Shell script already linked"

    echo -e "${COLOR_GREEN}Deployment script linked successfully. Run 'deploy' to deploy the stack.${NO_COLOR}"
fi

read -p "Do you want to initialize docker swarm? [y/N]: " INIT_SWARM

if [ "$INIT_SWARM" == "y" ]; then
    all_ips=$(ip a | grep "inet .* eth" | cut -d "/" -f 1 | cut -d "t" -f 2)

    # Display the IPs in a styled box
    box_width=50
    all_ips=$(ip a | grep "inet .* eth" | awk '{print $2}' | cut -d "/" -f 1)

    echo ""
    echo "╔$(printf '═%.0s' $(seq 1 $((box_width - 2))))╗"
    echo "║ $(printf '%-*s' $((box_width - 4)) "Available IP Addresses:") ║"
    echo "╠$(printf '═%.0s' $(seq 1 $((box_width - 2))))╣"
    for ip in $all_ips; do
        echo "║ $(printf '%-*s' $((box_width - 4)) "$ip") ║"
    done
    echo "╚$(printf '═%.0s' $(seq 1 $((box_width - 2))))╝"
    echo ""
    read -p "Enter the IP address of the manager node: " MANAGER_IP

    # Initialize docker swarm
    docker swarm init --advertise-addr "$MANAGER_IP"
fi

read -p "Do you want to assign labels to nodes? [y/N]: " ASSIGN_LABELS

if [ "$ASSIGN_LABELS" == "y" ]; then
  while true; do
    read -p "Enter the node name ( Empty to exit ): " NODE_NAME

    if [ -z "$NODE_NAME" ]; then
      break
    fi

    while true; do
      read -p "Enter the label ( Empty to exit ): " LABEL_NAME

      if [ -z "$LABEL_NAME" ]; then
        break
      fi

      docker node update --label-add "$LABEL_NAME=true" "$NODE_NAME"
    done
  done
fi
