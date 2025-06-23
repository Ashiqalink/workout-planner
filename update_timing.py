import csv
import os

# New timing data provided by user (in seconds)
new_timing_data = {
    "Push-ups": 30,
    "Squats": 30,
    "Plank": 30,
    "Tricep Dips": 30,
    "Lunges": 30,
    "Wall Sit": 30,
    "Superman": 30,
    "Glute Bridge": 30,
    "Incline Push-ups": 30,
    "Decline Push-ups": 40,
    "Standing Calf Raises": 30,
    "Reverse Lunges": 30,
    "Side Plank": 40,
    "Isometric Hold": 30,
    "Diamond Push-ups": 40,
    "Wide Push-ups": 30,
    "Single-leg Squat": 60,
    "Chair Squats": 30,
    "Step-ups": 30,
    "Arm Circles": 20,
    "Doorway Rows": 30,
    "Leg Raises": 30,
    "Mountain Climbers": 40,
    "Reverse Crunches": 30,
    "Superman Hold": 30,
    "Standing Side Leg Raises": 30,
    "Bridge March": 40,
    "Shoulder Taps": 30,
    "Static Lunge Hold": 30,
    "Tabletop Hold": 30,
    "Seated Knee Extensions": 30,
    "Dead Bug": 30,
    "High Knees": 30,
    "Butt Kicks": 30,
    "Jumping Jacks": 30,
    "Arm Swings": 20,
    "Hip Circles": 20,
    "Ankle Circles": 20,
    "Shoulder Rolls": 20,
    "Neck Stretches": 20,
    "Wrist Circles": 20,
    "Torso Twists": 20,
    "Leg Swings": 20,
    "Side Leg Swings": 20,
    "Walking Knee Hugs": 30,
    "Walking Heel to Butt": 30,
    "Walking High Kicks": 30,
    "Walking Lunges with Twist": 40,
    "Walking Arm Circles": 20,
    "Walking Toe Touches": 30,
    "Walking Side Steps": 30,
    "Walking Backward": 30,
    "Skipping": 30,
    "Carioca": 40,
    "Backpedaling": 30,
    "Zigzag Runs": 40,
    "Shuttle Runs": 40,
    "Sprint in Place": 30,
    "Side Shuffles": 30,
    "Grapevine": 40,
    "High Knee Skips": 30,
    "Backward Running": 40,
    "Brisk Walking": 120,
    "Jogging in Place": 60,
    "Marching in Place": 45,
    "Step Touch": 45,
    "Jump Rope (Imaginary)": 60,
    "Dancing": 90,
    "Shadow Boxing": 60,
    "Stair Climbing": 60,
    "Mountain Climbers": 45,
    "Burpees": 60,
    "High Knees": 30,
    "Butt Kicks": 30,
    "Jumping Jacks": 30,
    "Squat Jumps": 45,
    "Lunge Jumps": 45,
    "Plank to Push-up": 40,
    "Bear Crawls": 40,
    "Crab Walks": 40,
    "Inchworm Walk": 40,
    "Skaters": 40,
    "Squat to Overhead Reach": 30,
    "Lunge to Overhead Reach": 40,
    "Plank Jacks": 40,
    "Mountain Climber Twists": 60,
    "Burpee with Push-up": 60,
    "Squat to Overhead Press": 40,
    "Lunge to Overhead Press": 40,
    "Plank to Side Plank": 40,
    "Mountain Climber to Push-up": 60,
    "Ladder Drills (Imaginary)": 40,
    "Zigzag Runs": 40,
    "Shuttle Runs": 40,
    "Side Shuffles": 30,
    "Carioca": 40,
    "Backpedaling": 30,
    "Grapevine": 40,
    "Figure 8 Runs": 40,
    "Box Drills": 40,
    "T-Drill": 60,
    "Lateral Hops": 40,
    "Forward/Backward Hops": 40,
    "Diagonal Runs": 40,
    "Circle Runs": 30,
    "Sprint and Stop": 40,
    "Direction Changes": 40,
    "Reaction Drills": 60,
    "Mirror Drills": 40,
    "Shadow Drills": 40,
    "Cone Weaves": 40,
    "Lateral Bounds": 60,
    "Forward Bounds": 60,
    "Backward Bounds": 60,
    "Diagonal Bounds": 60,
    "Quick Feet": 30,
    "Lateral Quick Steps": 40,
    "Forward/Backward Quick Steps": 40,
    "Diagonal Quick Steps": 40,
    "Reaction Steps": 60,
    "Mirror Steps": 40,
    "Shadow Steps": 40,
    "Cone Steps": 40,
    "Dual Task Walking": 30,
    "Memory Sequence": 40,
    "Pattern Recognition": 40,
    "Reaction Time": 30,
    "Spatial Awareness": 40,
    "Coordination Challenge": 60,
    "Balance with Distraction": 40,
    "Movement Memory": 40,
    "Rhythm Recognition": 30,
    "Direction Changes": 40,
    "Movement Patterns": 60,
    "Balance Sequence": 40,
    "Coordination Sequence": 60,
    "Memory Walk": 40,
    "Pattern Walk": 40,
    "Reaction Walk": 30,
    "Spatial Walk": 40,
    "Coordination Walk": 60,
    "Balance Walk": 40,
    "Memory Sequence Walk": 60,
    "Pattern Recognition Walk": 40,
    "Reaction Time Walk": 30,
    "Spatial Awareness Walk": 40,
    "Coordination Challenge Walk": 60,
    "Balance with Distraction Walk": 40,
    "Movement Memory Walk": 40,
    "Rhythm Recognition Walk": 30,
    "Direction Changes Walk": 40,
    "Movement Patterns Walk": 60
}

def update_csv_timing():
    input_file = 'data/comprehensive_training_matrix.csv'
    output_file = 'data/comprehensive_training_matrix_updated.csv'
    
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found!")
        return
    
    updated_rows = []
    updated_count = 0
    
    with open(input_file, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        fieldnames = reader.fieldnames
        
        for row in reader:
            exercise_name = row['name']
            
            # Check if this exercise has new timing data
            if exercise_name in new_timing_data:
                # Convert seconds to minutes (round to 1 decimal place)
                new_duration = round(new_timing_data[exercise_name] / 60, 1)
                row['duration'] = str(new_duration)
                updated_count += 1
                print(f"Updated {exercise_name}: {new_timing_data[exercise_name]}s -> {new_duration}m")
            
            updated_rows.append(row)
    
    # Write the updated data back to the file
    with open(input_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_rows)
    
    print(f"\nSuccessfully updated {updated_count} exercises with new timing data!")
    print(f"File saved as: {input_file}")

if __name__ == "__main__":
    update_csv_timing() 