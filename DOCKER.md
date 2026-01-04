# Bus Tracker API - Docker Deployment Guide

## Quick Start

### 1. **Using docker-compose (Recommended)**

**Set your API key:**
```bash
export PAAC_API_KEY="nkAAE5Nd4sFMtyxthcEggx6h2"
```

**Start the container:**
```bash
docker-compose up -d
```

**View logs:**
```bash
docker-compose logs -f bus-tracker
```

**Stop the container:**
```bash
docker-compose down
```

---

### 2. **Using .env file (Best Practice)**

Create a `.env` file in the project directory:
```
PAAC_API_KEY=nkAAE5Nd4sFMtyxthcEggx6h2
BUS_ROUTE=13
STOP_ID=8175
STOP_NAME=Center Ave + Chalfonte Ave
```

Then run:
```bash
docker-compose up -d
```

---

### 3. **Manual Docker Commands**

**Build the image:**
```bash
docker build -t bus-tracker-api .
```

**Run the container:**
```bash
docker run -d \
  --name bus-tracker \
  -p 5001:5001 \
  -e PAAC_API_KEY="nkAAE5Nd4sFMtyxthcEggx6h2" \
  bus-tracker-api
```

**View logs:**
```bash
docker logs -f bus-tracker
```

**Stop the container:**
```bash
docker stop bus-tracker
docker rm bus-tracker
```

---

## Testing the Deployment

**Check if the API is running:**
```bash
curl http://localhost:5001/health
```

**Get predictions:**
```bash
curl http://localhost:5001/predictions
```

**Get West View predictions:**
```bash
curl http://localhost:5001/predictions/westview
```

**Get Downtown predictions:**
```bash
curl http://localhost:5001/predictions/downtown
```

---

## Production Deployment Options

### **1. Docker Hub (Free)**
```bash
docker tag bus-tracker-api your-username/bus-tracker-api:latest
docker push your-username/bus-tracker-api:latest
```

### **2. AWS (ECS)**
Push to Amazon ECR and deploy via ECS or Fargate.

### **3. Google Cloud Run (Serverless)**
```bash
gcloud run deploy bus-tracker \
  --source . \
  --platform managed \
  --region us-central1 \
  --set-env-vars PAAC_API_KEY="your_key"
```

### **4. Heroku**
```bash
heroku login
heroku create bus-tracker-api
heroku config:set PAAC_API_KEY="your_key"
git push heroku main
```

### **5. DigitalOcean App Platform**
1. Push to GitHub
2. Connect repository to DigitalOcean
3. Set environment variables in the dashboard
4. Deploy!

### **6. Self-Hosted (VPS with Docker)**
```bash
# On your server
ssh your-server
git clone https://github.com/your-username/dev-toolkit.git
cd dev-toolkit/bus-tracker
echo "PAAC_API_KEY=your_key" > .env
docker-compose up -d
```

---

## Docker Image Info

- **Base Image:** `python:3.11-slim` (lightweight)
- **Size:** ~150MB (slim image)
- **Port:** 5001 (configurable via `API_PORT` env var)
- **Health Check:** Enabled (checks `/health` endpoint every 30s)
- **Auto-restart:** Yes (`unless-stopped`)

---

## Troubleshooting

**Container won't start:**
```bash
docker logs bus-tracker
```

**API key not recognized:**
Check that `PAAC_API_KEY` is set in `.env` or docker-compose environment.

**Port already in use:**
```bash
# Use a different port
docker run -p 8080:5001 ...
# or in docker-compose
services:
  bus-tracker:
    ports:
      - "8080:5001"
```

**Need to rebuild image:**
```bash
docker-compose down
docker-compose up -d --build
```

---

## Docker Commands Cheat Sheet

```bash
# Build
docker build -t bus-tracker-api .

# Run
docker run -d -p 5001:5001 --name bus-tracker bus-tracker-api

# Logs
docker logs -f bus-tracker

# Shell access
docker exec -it bus-tracker /bin/bash

# Stop/Remove
docker stop bus-tracker
docker rm bus-tracker

# Docker Compose
docker-compose up -d          # Start
docker-compose down           # Stop
docker-compose ps             # List containers
docker-compose logs -f        # View logs
docker-compose restart        # Restart
```
