// Main JavaScript file for Student Training System

// Global variables
let currentUser = null;
let exercises = [];

// XSS-safe HTML escaping utility
function escapeHtml(str) {
    if (!str) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(str).replace(/[&<>"']/g, c => map[c]);
}

// Initialize application
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

function initializeApp() {
    // Add smooth scrolling
    addSmoothScrolling();
    
    // Add mobile menu toggle
    addMobileMenu();
    
    // Initialize tooltips
    initializeTooltips();
    
    // Add loading states
    addLoadingStates();
}

// Smooth scrolling for anchor links
function addSmoothScrolling() {
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        });
    });
}

// Mobile menu functionality
function addMobileMenu() {
    const nav = document.querySelector('.nav-menu');
    if (!nav) return;
    
    // Create mobile menu button
    const mobileMenuBtn = document.createElement('button');
    mobileMenuBtn.className = 'mobile-menu-btn';
    mobileMenuBtn.innerHTML = '☰';
    mobileMenuBtn.style.display = 'none';
    
    // Insert before nav menu
    nav.parentNode.insertBefore(mobileMenuBtn, nav);
    
    // Toggle menu
    mobileMenuBtn.addEventListener('click', function() {
        nav.classList.toggle('active');
        this.classList.toggle('active');
    });
    
    // Close menu when clicking outside
    document.addEventListener('click', function(e) {
        if (!nav.contains(e.target) && !mobileMenuBtn.contains(e.target)) {
            nav.classList.remove('active');
            mobileMenuBtn.classList.remove('active');
        }
    });
    
    // Show/hide mobile menu button based on screen size
    function toggleMobileMenu() {
        if (window.innerWidth <= 768) {
            mobileMenuBtn.style.display = 'block';
            nav.classList.add('mobile-menu');
        } else {
            mobileMenuBtn.style.display = 'none';
            nav.classList.remove('mobile-menu', 'active');
            mobileMenuBtn.classList.remove('active');
        }
    }
    
    toggleMobileMenu();
    window.addEventListener('resize', toggleMobileMenu);
}

// Initialize tooltips
function initializeTooltips() {
    const tooltipElements = document.querySelectorAll('[data-tooltip]');
    
    tooltipElements.forEach(element => {
        element.addEventListener('mouseenter', showTooltip);
        element.addEventListener('mouseleave', hideTooltip);
    });
}

function showTooltip(e) {
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    tooltip.textContent = e.target.getAttribute('data-tooltip');
    document.body.appendChild(tooltip);
    
    const rect = e.target.getBoundingClientRect();
    tooltip.style.left = rect.left + (rect.width / 2) - (tooltip.offsetWidth / 2) + 'px';
    tooltip.style.top = rect.top - tooltip.offsetHeight - 10 + 'px';
    
    e.target.tooltip = tooltip;
}

function hideTooltip(e) {
    if (e.target.tooltip) {
        e.target.tooltip.remove();
        e.target.tooltip = null;
    }
}

// Loading states — only for buttons that opt in via data-loading
function addLoadingStates() {
    const buttons = document.querySelectorAll('.btn[data-loading]');
    
    buttons.forEach(button => {
        button.addEventListener('click', function() {
            if (this.classList.contains('loading')) {
                return;
            }
            
            const originalHTML = this.innerHTML;
            this.innerHTML = 'Loading...';
            this.classList.add('loading');
            this.disabled = true;
            
            // Reset after 3 seconds (fallback)
            setTimeout(() => {
                this.innerHTML = originalHTML;
                this.classList.remove('loading');
                this.disabled = false;
                if (typeof lucide !== 'undefined') lucide.createIcons({ root: this });
            }, 3000);
        });
    });
}

// API helper functions
async function apiRequest(url, options = {}) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API request failed:', error);
        throw error;
    }
}

// Exercise management
async function loadExercises() {
    try {
        const data = await apiRequest('/api/exercises');
        exercises = data.exercises;
        return exercises;
    } catch (error) {
        console.error('Failed to load exercises:', error);
        return [];
    }
}

// Workout management
async function generateWorkout(workoutData) {
    try {
        const data = await apiRequest('/api/generate-workout', {
            method: 'POST',
            body: JSON.stringify(workoutData)
        });
        return data;
    } catch (error) {
        console.error('Failed to generate workout:', error);
        throw error;
    }
}

// Session management
async function saveSession(sessionData) {
    try {
        const data = await apiRequest('/api/complete_session', {
            method: 'POST',
            body: JSON.stringify(sessionData)
        });
        return data;
    } catch (error) {
        console.error('Failed to save session:', error);
        throw error;
    }
}

// Utility functions
function formatTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
}

function formatDate(date) {
    return new Date(date).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    document.body.appendChild(notification);
    
    // Animate in
    setTimeout(() => {
        notification.classList.add('show');
    }, 100);
    
    // Remove after 5 seconds
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => {
            notification.remove();
        }, 300);
    }, 5000);
}

// Local storage helpers
function saveToLocalStorage(key, data) {
    try {
        localStorage.setItem(key, JSON.stringify(data));
    } catch (error) {
        console.error('Failed to save to localStorage:', error);
    }
}

function getFromLocalStorage(key) {
    try {
        const data = localStorage.getItem(key);
        return data ? JSON.parse(data) : null;
    } catch (error) {
        console.error('Failed to load from localStorage:', error);
        return null;
    }
}

// Session storage helpers
function saveToSessionStorage(key, data) {
    try {
        sessionStorage.setItem(key, JSON.stringify(data));
    } catch (error) {
        console.error('Failed to save to sessionStorage:', error);
    }
}

function getFromSessionStorage(key) {
    try {
        const data = sessionStorage.getItem(key);
        return data ? JSON.parse(data) : null;
    } catch (error) {
        console.error('Failed to load from sessionStorage:', error);
        return null;
    }
}

// Chart.js configuration
function createChart(canvasId, type, data, options = {}) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    
    const defaultOptions = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                position: 'bottom'
            }
        }
    };
    
    return new Chart(ctx, {
        type: type,
        data: data,
        options: { ...defaultOptions, ...options }
    });
}

// Export functions for use in templates
window.TrainingApp = {
    loadExercises,
    generateWorkout,
    saveSession,
    formatTime,
    formatDate,
    showNotification,
    saveToLocalStorage,
    getFromLocalStorage,
    saveToSessionStorage,
    getFromSessionStorage,
    createChart
}; 