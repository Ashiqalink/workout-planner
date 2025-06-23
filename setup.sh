#!/bin/bash
# Student Training System - Automated Setup Script
# This script sets up the complete Flask application environment

echo "🏋️  Student Training System Setup"
echo "================================="
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8+ first."
    echo "   On Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Create project directory structure
echo "📁 Creating project directory structure..."
mkdir -p data
mkdir -p static
mkdir -p templates
mkdir -p instance

echo "✅ Directory structure created"

# Create virtual environment
echo "🔧 Creating Python virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "🚀 Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

echo "✅ Dependencies installed successfully"

# Create instance configuration if it doesn't exist
if [ ! -f "instance/config.py" ]; then
    echo "⚙️  Creating instance configuration..."
    cat > instance/config.py << 'EOF'
import os
from datetime import timedelta

# Instance-specific configuration
SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
DATABASE_URL = os.environ.get('DATABASE_URL') or 'sqlite:///training_app.db'

# Session configuration
PERMANENT_SESSION_LIFETIME = timedelta(days=7)

# Development settings
DEBUG = True
TESTING = False

# Security settings for production
# Uncomment and modify for production deployment
# CSRF_ENABLED = True
# WTF_CSRF_ENABLED = True
EOF
    echo "✅ Instance configuration created"
fi

# Create .gitignore
if [ ! -f ".gitignore" ]; then
    echo "📝 Creating .gitignore..."
    cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST

# Virtual Environment
venv/
env/
ENV/

# Database
*.db
*.sqlite3

# Instance folder
instance/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Logs
*.log

# Environment variables
.env
.env.local
.env.development.local
.env.test.local
.env.production.local
EOF
    echo "✅ .gitignore created"
fi

# Create manifest.json for PWA features
if [ ! -f "static/manifest.json" ]; then
    echo "📱 Creating PWA manifest..."
    cat > static/manifest.json << 'EOF'
{
  "name": "Student Training System",
  "short_name": "TrainingApp",
  "description": "Comprehensive fitness training across 5 domains",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#fcfcf9",
  "theme_color": "#21808d",
  "icons": [
    {
      "src": "https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.0.0/svgs/solid/dumbbell.svg",
      "sizes": "any",
      "type": "image/svg+xml"
    }
  ]
}
EOF
    echo "✅ PWA manifest created"
fi

# Initialize database and load sample data
echo "🗄️  Initializing database..."
python3 -c "
import sys
sys.path.append('.')
from app import init_db, load_exercise_data
init_db()
load_exercise_data()
print('Database initialized successfully')
"

echo ""
echo "🎉 Setup Complete!"
echo "=================="
echo ""
echo "Your Student Training System is ready to use!"
echo ""
echo "To start the application:"
echo "1. Activate the virtual environment: source venv/bin/activate"
echo "2. Run the Flask app: python3 app.py"
echo "3. Open your browser to: http://localhost:5000"
echo ""
echo "For development:"
echo "- The app runs in debug mode by default"
echo "- Database file: training_app.db"
echo "- Static files: static/ directory"
echo "- Templates: templates/ directory"
echo ""
echo "To deactivate virtual environment: deactivate"
echo ""
echo "Happy training! 💪"