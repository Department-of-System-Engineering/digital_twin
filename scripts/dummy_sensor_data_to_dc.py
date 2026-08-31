import json
import random
from datetime import datetime, timedelta

# Define the starting time (15 minutes before the reference time 2026-08-25 15:26:32)
start_time = datetime(2026, 8, 25, 15, 11, 32)
generated_list = []

# Generate exactly 200 elements
for step in range(200):
    # Calculate the timestamp for the current step (10 minute intervals)
    current_time = start_time + timedelta(minutes=10 * step)
    
    # Generate a random float value formatted as string with 2 decimal places
    random_value = f"{random.uniform(37.0, 53.0):.2f}"
    
    # Create the dictionary based on the provided sample
    item_dict = {
        "technicalObjectUniqueIdentifier": "pe teszt",
        "metricFunctionUniqueIdentifier": "teszt",
        "value": random_value,
        "createdAt": current_time.strftime("%Y-%m-%d %H:%M:%S.000"),
        "tags": {}
    }
    
    generated_list.append(item_dict)

# Output the resulting list as a JSON string
print(json.dumps(generated_list, indent=2))