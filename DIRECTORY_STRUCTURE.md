# Student Training System - Directory Structure

```
student-training-system/
├── app.py                              # Main Flask application file
├── config.py                           # Configuration settings
├── requirements.txt                    # Python dependencies
├── README.md                          # Setup and usage instructions
├── training_app.db                    # SQLite database (created automatically)
│
├── data/                              # CSV data files
│   ├── comprehensive_training_matrix.csv
│   ├── complete_4week_program.csv
│   └── student_training_program.csv
│
├── static/                            # Static files (CSS, JS, images)
│   ├── css/
│   │   └── style.css                  # Main stylesheet
│   ├── js/
│   │   └── app.js                     # Main JavaScript file
│   └── images/
│       ├── favicon.ico
│       ├── training_progress_chart.jpg
│       └── training_matrix_chart.jpg
│
└── templates/                         # HTML templates
    ├── base.html                      # Base template
    ├── index.html                     # Dashboard page
    ├── library.html                   # Exercise library
    ├── planner.html                   # Training planner
    ├── progress.html                  # Progress tracking
    ├── login.html                     # User login
    └── register.html                  # User registration
```

## File Descriptions

### Core Application Files
- **app.py**: Main Flask application with routes, database operations, and API endpoints
- **config.py**: Configuration settings for different environments
- **requirements.txt**: List of Python packages needed to run the application

### Data Files
- **comprehensive_training_matrix.csv**: Complete exercise database with categories, durations, benefits
- **complete_4week_program.csv**: High-level 4-week training program structure
- **student_training_program.csv**: Detailed daily training sessions with specific exercises

### Static Files
- **static/css/style.css**: Complete CSS styling with responsive design
- **static/js/app.js**: JavaScript for interactive features, timers, and session management
- **static/images/**: Training charts and favicon

### Templates
- **base.html**: Base template with navigation and common structure
- **index.html**: Dashboard showing progress overview and today's session
- **library.html**: Browse and filter all available exercises
- **planner.html**: Create custom workouts based on preferences
- **progress.html**: Track training history with charts and statistics
- **login.html**: User authentication login form
- **register.html**: New user registration form

## Key Features

### 1. Dashboard
- Weekly progress overview
- Domain-specific training statistics
- Today's recommended session
- Quick start templates
- Achievement system

### 2. Exercise Library
- Browse 31 exercises across 5 domains
- Filter by category and search
- Detailed exercise information
- Difficulty levels and benefits

### 3. Training Planner
- Custom workout generation
- Select multiple training domains
- Adjust duration (5-45 minutes)
- Choose difficulty level
- Special focus options

### 4. Progress Tracking
- Visual charts showing weekly progression
- Domain distribution analysis
- Personal training statistics
- Recent session history

### 5. Training Sessions
- Interactive exercise timer
- Step-by-step exercise guidance
- Progress tracking within sessions
- Session completion recording

### 6. User Management
- Guest mode for immediate access
- User registration and authentication
- Personal progress persistence
- Session management
