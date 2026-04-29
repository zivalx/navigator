#!/bin/bash

# Navigator API Start Script

echo "🚀 Starting Navigator API..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Copying from .env.example..."
    cp .env.example .env
    echo "✅ Created .env file. Please add your API keys before continuing."
    exit 1
fi

# Start with Docker Compose
if command -v docker-compose &> /dev/null; then
    echo "🐳 Starting services with Docker Compose..."
    docker-compose up -d

    echo ""
    echo "✅ Services started!"
    echo ""
    echo "📊 API: http://localhost:7000"
    echo "📚 Docs: http://localhost:7000/docs"
    echo "🗄️  Database: postgresql://postgres:postgres@localhost:5432/navigator"
    echo "🔴 Redis: redis://localhost:6379/0"
    echo ""
    echo "To view logs: docker-compose logs -f api"
    echo "To stop: docker-compose down"
else
    echo "❌ Docker Compose not found. Please install Docker or run manually."
    echo ""
    echo "Manual setup:"
    echo "  1. pip install -r requirements.txt"
    echo "  2. Start PostgreSQL and Redis"
    echo "  3. python seed.py"
    echo "  4. uvicorn app.main:app --reload"
fi
