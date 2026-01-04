#!/bin/bash

# Bus Tracker Setup Script
# This script helps you set up and run the Pittsburgh Bus Tracker

set -e

echo "🚌 Pittsburgh Bus Tracker Setup"
echo "================================"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed."
    echo "Please install Docker from: https://www.docker.com/get-started"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed."
    echo "Please install Docker Compose"
    exit 1
fi

echo "✅ Docker is installed"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "✅ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env and add your TrueTime API key"
    echo "   Get a free API key at: https://truetime.portauthority.org/"
    echo ""
    read -p "Press Enter to continue without API key (will use GTFS-RT) or Ctrl+C to exit and add key..."
    echo ""
else
    echo "✅ .env file exists"
    echo ""
fi

# Ask user what they want to do
echo "What would you like to do?"
echo "1) Start the API (Docker)"
echo "2) Start the API + Dashboard (requires Python/Streamlit)"
echo "3) Stop the API"
echo "4) View logs"
echo "5) Run tests"
echo ""
read -p "Enter your choice (1-5): " choice

case $choice in
    1)
        echo ""
        echo "🚀 Starting Bus Tracker API..."
        docker-compose up -d --build
        echo ""
        echo "✅ API is running!"
        echo "   Test it: curl http://localhost:5001/health"
        echo "   View logs: docker-compose logs -f"
        echo "   Stop it: docker-compose down"
        ;;
    2)
        echo ""
        echo "🚀 Starting API..."
        docker-compose up -d --build
        
        echo ""
        echo "📊 Starting Dashboard..."
        
        # Check if streamlit is installed
        if ! command -v streamlit &> /dev/null; then
            echo "Installing streamlit..."
            pip install streamlit
        fi
        
        echo ""
        echo "✅ Opening dashboard in browser..."
        echo "   API: http://localhost:5001"
        echo "   Dashboard: http://localhost:8501"
        echo ""
        streamlit run dashboard.py
        ;;
    3)
        echo ""
        echo "🛑 Stopping Bus Tracker..."
        docker-compose down
        echo "✅ Stopped"
        ;;
    4)
        echo ""
        echo "📋 Viewing logs (Ctrl+C to exit)..."
        docker-compose logs -f
        ;;
    5)
        echo ""
        echo "🧪 Running tests..."
        echo "Testing API health..."
        curl -f http://localhost:5001/health || echo "❌ API is not running. Start it first with option 1."
        echo ""
        echo "Testing predictions..."
        curl -f http://localhost:5001/predictions || echo "❌ API is not running."
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "Done! 🎉"
