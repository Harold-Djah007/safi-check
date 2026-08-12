#!/usr/bin/env bash
# exit on error
set -o errexit

# Install system dependencies for ODBC
apt-get update
apt-get install -y \
    curl \
    gnupg \
    unixodbc-dev \
    g++ \
    gcc \
    python3-dev

# Install Microsoft ODBC Driver
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/ubuntu/20.04/prod.list > /etc/apt/sources.list.d/mssql-release.list
apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql18

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt
