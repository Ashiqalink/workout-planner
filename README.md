# Student Training System 🏋️‍♂️

A comprehensive Flask-based web application for student fitness training that covers five essential domains: **Strength**, **Speed & Mobility**, **Endurance**, **Agility**, and **Cognitive Training**.

## 🌟 Features

### 🏠 Dashboard
- **Personal progress overview** with weekly statistics
- **Quick-start templates** for different times of day
- **Domain-specific progress tracking** across all 5 training areas
- **Streak tracking** and achievement monitoring

### 📚 Exercise Library
- **30+ exercises** across all five training domains
- **Advanced filtering** by category, difficulty, and search
- **Detailed instructions** and equipment requirements
- **Progressive difficulty levels** from beginner to advanced

### 📋 Training Planner
- **Custom workout generation** based on available time and preferences
- **Intelligent exercise selection** with automatic rest periods
- **Flexible duration settings** from 5-45 minutes
- **Multi-domain training** combinations

### 📊 Progress Tracking
- **Interactive charts** showing weekly progression
- **Domain distribution analytics** with Chart.js visualizations
- **Achievement system** with unlockable badges
- **Session history** and statistics

### ⏱️ Interactive Sessions
- **Real-time timers** for each exercise
- **Step-by-step guidance** with exercise instructions
- **Progress tracking** within sessions
- **Automatic session recording** and data persistence

### 👤 User Management
- **Guest mode** for immediate access
- **User registration** and authentication
- **Session persistence** across devices
- **Secure password hashing** with Werkzeug

## 🚀 Quick Start

### Option 1: Automated Setup (Recommended)

```bash
# Clone or download the project files
# Navigate to the project directory
cd student-training-system

# Run the automated setup script
chmod +x setup.sh
./setup.sh

# Start the application
source venv/bin/activate
python3 app.py
```

### Option 2: Manual Setup

#### Prerequisites
- **Python 3.8+** installed on your system
- **pip** package manager
- **Git** (optional, for cloning)

#### Step-by-Step Installation

1. **Create project directory and navigate to it:**
   ```bash
   mkdir student-training-system
   cd student-training-system
   ```

2. **Copy all project files to this directory**

3. **Create virtual environment:**
   ```bash
   python3 -m venv venv
   ```

4. **Activate virtual environment:**
   ```bash
   # On Linux/Mac:
   source venv/bin/activate

   # On Windows:
   venv\Scripts\activate
   ```

5. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

6. **Initialize the database:**
   ```bash
   python3 -c "from app import init_db, load_exercise_data; init_db(); load_exercise_data()"
   ```

7. **Start the application:**
   ```bash
   python3 app.py
   ```

8. **Open your browser and navigate to:**
   ```
   http://localhost:5000
   ```

## 📁 Project Structure

```
student-training-system/
├── app.py                              # Main Flask application
├── config.py                           # Configuration settings
├── requirements.txt                    # Python dependencies
├── setup.sh                            # Automated setup script
├── README.md                           # This file
├── comprehensive_training_matrix.csv   # Exercise database
├── complete_4week_program.csv          # Training program structure
│
├── templates/                          # HTML templates
│   ├── base.html                       # Base template layout
│   ├── index.html                      # Dashboard page
│   ├── library.html                    # Exercise library
│   ├── planner.html                    # Workout planner
│   ├── progress.html                   # Progress tracking
│   ├── session.html                    # Training sessions
│   ├── login.html                      # User login
│   └── register.html                   # User registration
│
├── static/                             # Static files
│   ├── style.css                       # Comprehensive styling
│   ├── app.js                          # Frontend JavaScript
│   └── manifest.json                   # PWA manifest
│
├── data/                               # Data files directory
│   └── (CSV files loaded automatically)
│
└── instance/                           # Instance-specific config
    └── config.py                       # Local configuration
```

## 🎯 Training Domains

### 💪 Strength
- **Push-ups, Squats, Planks, Lunges**
- Progressive bodyweight exercises
- Core stability and functional movement
- **8 exercises** with varying difficulty levels

### ⚡ Speed & Mobility
- **High Knees, Dynamic Stretching, Leg Swings**
- Joint mobility and movement preparation
- Speed development and coordination
- **6 exercises** for flexibility and agility

### 🏃 Endurance
- **Jumping Jacks, Mountain Climbers, Burpees**
- Cardiovascular conditioning
- Metabolic training and stamina building
- **5 exercises** for heart health

### 🤸 Agility
- **Ladder Drills, Direction Changes, Balance**
- Coordination and reaction training
- Spatial awareness and quick movements
- **5 exercises** for athletic performance

### 🧠 Cognitive
- **Dual N-Back, Mental Math, Mindfulness**
- Working memory and focus enhancement
- Problem-solving and attention training
- **6 exercises** for mental performance

## 🔧 Technical Details

### Backend Technologies
- **Flask 2.3.3** - Web framework
- **SQLite** - Database for data persistence
- **Pandas** - Data processing and CSV handling
- **Werkzeug** - Security and password hashing

### Frontend Technologies
- **HTML5** with semantic markup
- **CSS3** with custom properties and responsive design
- **Vanilla JavaScript** with ES6+ features
- **Chart.js** for data visualization
- **Font Awesome** for icons

### Database Schema
- **Users table** - Authentication and user data
- **Exercises table** - Exercise library from CSV
- **User sessions** - Completed workout tracking
- **User progress** - Domain-specific statistics

### API Endpoints
- `GET /api/exercises` - Retrieve all exercises
- `POST /api/generate_workout` - Create custom workouts
- `POST /api/complete_session` - Record completed sessions
- `GET /api/user_stats` - Fetch user statistics
- `GET /api/achievements` - Get achievement status

## 🎨 User Interface

### Responsive Design
- **Mobile-first approach** with touch-optimized controls
- **CSS Grid and Flexbox** for modern layouts
- **Dark and light mode** support with CSS custom properties
- **Progressive Web App** features with manifest.json

### Interactive Elements
- **Real-time timers** with start/pause functionality
- **Modal dialogs** for focused training sessions
- **Progress bars** and visual feedback
- **Smooth transitions** and hover effects

## 📊 Data Management

### Exercise Data
- **CSV-based exercise library** for easy modification
- **Automatic database population** from CSV files
- **Structured exercise metadata** with instructions and equipment
- **Category-based organization** across all domains

### User Progress
- **Session tracking** with exercise completion
- **Statistical analysis** with weekly and domain metrics
- **Achievement system** with progress milestones
- **Data persistence** across browser sessions

## 🔒 Security Features

- **Password hashing** with Werkzeug security
- **Session management** with secure cookies
- **SQL injection prevention** with parameterized queries
- **Input validation** and sanitization
- **CSRF protection** ready for production

## 🚀 Deployment Options

### Local Development
- **Debug mode enabled** for development
- **Auto-reload** on file changes
- **Comprehensive error logging**

### Production Deployment
- **Environment-based configuration**
- **Security settings** for production use
- **Static file optimization**
- **Database migration** support

## 📈 Extension Possibilities

### Easy Customization
- **Modular exercise system** - Add new exercises via CSV
- **Configurable training programs** - Modify 4-week progression
- **Theme customization** - Update CSS custom properties
- **API extensibility** - Add new endpoints easily

### Advanced Features
- **Wearable device integration** - Heart rate and activity tracking
- **Social features** - Friend challenges and leaderboards
- **Nutrition tracking** - Meal planning and calorie counting
- **Video integration** - Exercise demonstration videos

## 🐛 Troubleshooting

### Common Issues

**Port already in use:**
```bash
# Kill process using port 5000
sudo lsof -t -i tcp:5000 | xargs kill -9
```

**Database errors:**
```bash
# Reset database
rm training_app.db
python3 -c "from app import init_db, load_exercise_data; init_db(); load_exercise_data()"
```

**Permission errors:**
```bash
# Fix script permissions
chmod +x setup.sh
```

**Python version issues:**
```bash
# Check Python version (requires 3.8+)
python3 --version
```

## 📝 Contributing

### Adding New Exercises
1. Edit `comprehensive_training_matrix.csv`
2. Add new rows with required fields:
   - `name`, `category`, `difficulty`, `duration`
   - `description`, `instructions`, `equipment`, `target_muscles`
3. Restart the application to load new data

### Modifying UI
1. Edit templates in `templates/` directory
2. Update styles in `static/style.css`
3. Add functionality in `static/app.js`

### Database Changes
1. Modify schema in `init_db()` function
2. Create migration scripts if needed
3. Test with fresh database initialization

## 📚 Educational Value

This project demonstrates:
- **Full-stack web development** with Flask and modern frontend
- **Database design** and data persistence patterns
- **User experience design** for fitness applications
- **Responsive web design** and accessibility
- **API development** and JavaScript integration
- **Security best practices** in web applications

## 🏆 Achievement System

- **First Steps** - Complete your first session
- **Consistency Champion** - Complete 10 sessions
- **Hour Hero** - Train for 60+ minutes total
- **Well Rounded** - Train in all 5 domains
- **Streak Master** - Maintain a 7-day streak
- **Dedication** - Complete 50 total sessions

## 📞 Support

For issues or questions:
1. Check the troubleshooting section above
2. Review the project structure and code comments
3. Ensure all dependencies are properly installed
4. Verify Python version compatibility (3.8+)

## 📄 License

This project is designed for educational purposes and personal use. Feel free to modify and extend according to your needs.

---

**Start your fitness journey today!** 🚀💪

Open your browser to `http://localhost:5000` and begin training across all five essential domains.